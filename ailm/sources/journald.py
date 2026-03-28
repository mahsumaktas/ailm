"""Journald log monitor — pre-filters and batches journal entries.

Requires python-systemd (system package: `sudo pacman -S python-systemd`).
Gracefully disabled when not available.
"""

import asyncio
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from ailm.core.bus import EventBus
from ailm.core.dedup import DedupAction, DedupDecision, EventDedup, fingerprint
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import cancel_task

logger = logging.getLogger(__name__)

try:
    from systemd import journal  # type: ignore[import-untyped]

    HAS_SYSTEMD = True
except ImportError:
    HAS_SYSTEMD = False

# Comprehensive prefilter — catch every meaningful system event.
# Grouped by category for maintainability.
PREFILTER_RE = re.compile(
    r"(?i)\b("
    # Core errors
    r"error|critical|fail|denied|panic|fatal|abort|corrupt|"
    r"emergency|alert|warning|degraded|"
    # Process/memory
    r"oom|segfault|killed|coredump|core dump|"
    r"out of memory|cannot allocate|memory pressure|"
    # Network/auth
    r"timeout|refused|unreachable|dropped|"
    r"invalid user|authentication fail|permission denied|"
    r"ban |unban |brute.force|unauthorized|"
    r"connection reset|handshake fail|certificate.*error|"
    # GPU/hardware
    r"NVRM|Xid|nvrm|gpu.*hang|PCIe.*error|bus_lock|split_lock|"
    r"hardware error|machine check|thermal|overheat|throttl|"
    # Disk/filesystem
    r"I/O error|read.only|remount|filesystem|fsck|"
    r"BTRFS|EXT4.*error|XFS.*error|"
    r"no space left|disk full|quota|"
    # Systemd lifecycle (only failures, not normal start/stop)
    r"start-limit|entered failed|main process exited|"
    r"unit.*failed|"
    # USB/bluetooth/audio
    r"usb.*disconnect|usb.*reset|usb.*overcurrent|"
    r"bluetooth.*fail|bluetooth.*error|btusb|"
    r"pipewire.*error|pulseaudio.*fail|alsa.*error|"
    # Session/login (failures + power state changes)
    r"login.*fail|pam_unix.*fail|"
    r"suspend|resume|hibernate|lid |"
    # Firewall
    r"iptables|nftables|DROP|REJECT|firewall|"
    # Kernel modules
    r"module.*load|insmod|modprobe|module.*fail|"
    # Wayland/display
    r"compositor|wayland.*error|wlroots|kwin.*crash|plasmashell|"
    # Cron/scheduled (only errors, not normal execution)
    r"cron.*error|anacron.*fail|logrotate.*error|"
    # Package/update
    r"pacman|ALPM|upgrade|downgrad|"
    # Snapper/snapshot (only actual snapshot events, not DBus lifecycle)
    r"snapper.*error|snapshot.*creat|snapshot.*delet"
    r")"
)

BATCH_SECONDS = 5
BUFFER_MAXLEN = 5000
FLUSH_YIELD_EVERY = 100  # yield to event loop every N publishes


def compile_noise_filter(patterns: list[str]) -> re.Pattern | None:
    """Compile a list of regex patterns into a single combined pattern."""
    if not patterns:
        return None
    combined = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(combined, re.IGNORECASE)

_PRIORITY_MAP: dict[int, Severity] = {
    0: Severity.CRITICAL,  # EMERG
    1: Severity.CRITICAL,  # ALERT
    2: Severity.CRITICAL,  # CRIT
    3: Severity.CRITICAL,  # ERR
    4: Severity.WARNING,   # WARNING
    5: Severity.INFO,      # NOTICE
    6: Severity.INFO,      # INFO
    7: Severity.INFO,      # DEBUG
}


@dataclass
class JournalEntry:
    """Buffered journald entry waiting to be published as a system event."""

    message: str
    unit: str
    priority: int
    timestamp: datetime

    def __repr__(self) -> str:
        """Return a concise representation of the buffered entry."""
        return (
            "JournalEntry("
            f"unit={self.unit!r}, "
            f"priority={self.priority!r}, "
            f"message={self.message!r}, "
            f"timestamp={self.timestamp.isoformat()!r})"
        )


def priority_to_severity(priority: int) -> Severity:
    """Map a journald numeric priority to the corresponding event severity."""
    return _PRIORITY_MAP.get(priority, Severity.INFO)


def matches_prefilter(message: str) -> bool:
    """Return whether a journal message is interesting enough to buffer."""
    return PREFILTER_RE.search(message) is not None


class JournaldSource:
    """Journald event source with regex pre-filter, dedup, and batched dispatch."""

    name = "journald"

    def __init__(
        self,
        batch_seconds: float = BATCH_SECONDS,
        dedup: EventDedup | None = None,
        startup_grace_seconds: float = 10.0,
        noise_patterns: list[str] | None = None,
    ) -> None:
        if batch_seconds <= 0:
            raise ValueError("batch_seconds must be positive")
        self._batch_seconds = batch_seconds
        self._dedup = dedup
        self._startup_grace = startup_grace_seconds
        self._noise_re = compile_noise_filter(noise_patterns or [])
        self._start_time: float = 0.0
        self._bus: EventBus | None = None
        self._buffer: deque[JournalEntry] = deque(maxlen=BUFFER_MAXLEN)
        self._urgent: bool = False  # set by reader thread when critical entry arrives
        self._reader_task: asyncio.Task[None] | None = None
        self._batcher_task: asyncio.Task[None] | None = None
        self._stop_event = threading.Event()

    @property
    def bus(self) -> EventBus:
        """Return the bound event bus or raise if the source is not started."""
        if self._bus is None:
            raise RuntimeError(f"Source '{self.name}' not started — call start() first")
        return self._bus

    async def start(self, bus: EventBus) -> None:
        """Start background reader and batcher tasks when systemd support exists."""
        if not HAS_SYSTEMD:
            logger.warning("python-systemd not installed, journald source disabled")
            return

        self._bus = bus
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._reader_task = asyncio.create_task(self._run_reader())
        self._batcher_task = asyncio.create_task(self._run_batcher())

    async def stop(self) -> None:
        """Stop reader and batcher tasks, then flush any buffered entries."""
        self._stop_event.set()
        await cancel_task(self._reader_task)
        self._reader_task = None
        await cancel_task(self._batcher_task)
        self._batcher_task = None

        # Flush remaining — guard against _bus=None (start never called)
        if self._bus is not None:
            await self._flush_buffer()

    async def _run_reader(self) -> None:
        await asyncio.to_thread(self._reader_loop)

    def _reader_loop(self) -> None:
        """Blocking journal reader — runs in a dedicated thread."""
        try:
            reader = journal.Reader()
            reader.this_boot()
            reader.seek_tail()
            reader.get_previous()
        except Exception:
            logger.exception("Failed to initialize journal reader")
            return

        try:
            while not self._stop_event.is_set():
                result = reader.wait(1.0)
                if result == journal.APPEND:
                    for entry in reader:
                        msg = entry.get("MESSAGE", "")
                        if not msg:
                            continue

                        priority = int(entry.get("PRIORITY", 6))
                        transport = entry.get("_TRANSPORT", "")

                        # Skip ailm's own log messages
                        if entry.get("_SYSTEMD_USER_UNIT", "") == "ailm.service":
                            continue
                        if entry.get("SYSLOG_IDENTIFIER", "") in ("ailm",):
                            continue

                        # Decision: should this entry be captured?
                        # 1. Kernel messages → ALWAYS capture (OOM, Xid, panic)
                        # 2. Priority 0-4 (EMERG-WARNING) → ALWAYS capture
                        # 3. Priority 5-7 (NOTICE-DEBUG) → only if prefilter matches
                        is_kernel = transport == "kernel"
                        is_urgent = priority <= 4

                        if not is_kernel and not is_urgent:
                            if not matches_prefilter(msg):
                                continue

                        # Noise filter (skip known harmless even if priority is low)
                        if self._noise_re is not None and self._noise_re.search(msg):
                            if not is_kernel and priority > 3:
                                continue  # never filter kernel EMERG/ALERT/CRIT

                        ts = entry.get("__REALTIME_TIMESTAMP")
                        if ts is None:
                            ts = datetime.now(timezone.utc)
                        elif ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)

                        unit = entry.get("_SYSTEMD_UNIT", "")
                        if not unit and is_kernel:
                            unit = "kernel"

                        je = JournalEntry(
                            message=msg,
                            unit=unit or "unknown",
                            priority=priority,
                            timestamp=ts,
                        )
                        self._buffer.append(je)
                        if priority <= 2:  # EMERG/ALERT/CRIT → urgent flush
                            self._urgent = True
        except Exception:
            logger.exception("Journal reader loop error")
        finally:
            reader.close()

    async def _run_batcher(self) -> None:
        """Flush buffered entries. Polls every 0.5s for urgent, full flush every batch_seconds."""
        ticks = 0
        interval = 0.5  # fast poll for urgent events
        batch_ticks = int(self._batch_seconds / interval)
        while True:
            await asyncio.sleep(interval)
            ticks += 1
            # Urgent: kernel EMERG/ALERT/CRIT arrived → flush immediately
            if self._urgent:
                self._urgent = False
                await self._flush_buffer()
            # Normal: flush every batch_seconds
            elif ticks >= batch_ticks:
                ticks = 0
                await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        if not self._buffer:
            return

        # Drain with popleft — thread-safe, no race between list()+clear()
        entries: list[JournalEntry] = []
        while self._buffer:
            try:
                entries.append(self._buffer.popleft())
            except IndexError:
                break  # reader thread drained it simultaneously

        # Startup grace: only emit CRITICAL during first N seconds
        in_grace = time.monotonic() - self._start_time < self._startup_grace
        if in_grace:
            entries = [e for e in entries if e.priority <= 2]  # EMERG/ALERT/CRIT only

        published = 0
        suppressed = 0
        for i, entry in enumerate(entries):
            # Dedup: fingerprint and decide
            if self._dedup is not None:
                fp = fingerprint(self.name, entry.unit, entry.message)
                decision = self._dedup.should_publish(fp, self.name, entry.message)
                if decision.action == DedupAction.SUPPRESS:
                    suppressed += 1
                    continue
                if decision.action == DedupAction.AGGREGATE:
                    if decision.aggregate_summary is not None:
                        # Time for periodic aggregate — emit summary event
                        raw = decision.aggregate_summary
                    else:
                        suppressed += 1
                        continue  # suppress individual, no summary yet
                else:
                    raw = f"unit={entry.unit} priority={entry.priority} msg={entry.message}"
                    if decision.suppressed_count > 0:
                        raw += f" [+{decision.suppressed_count} suppressed]"
            else:
                raw = f"unit={entry.unit} priority={entry.priority} msg={entry.message}"

            event = SystemEvent(
                type=EventType.LOG_ANOMALY,
                severity=priority_to_severity(entry.priority),
                raw_data=raw,
                source=self.name,
                summary=None,  # LLM classification fills this later
                timestamp=entry.timestamp,
            )
            await self.bus.publish(event)
            published += 1
            # Yield to event loop periodically — prevents bus queue overflow
            if published % FLUSH_YIELD_EVERY == FLUSH_YIELD_EVERY - 1:
                await asyncio.sleep(0)

        if entries:
            logger.debug(
                "Flush: %d entries, %d published, %d suppressed, %d tracked fps",
                len(entries), published, suppressed,
                self._dedup.tracked_count if self._dedup else 0,
            )

