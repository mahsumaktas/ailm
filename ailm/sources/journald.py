"""Journald log monitor — pre-filters and batches journal entries.

Requires python-systemd (system package: `sudo pacman -S python-systemd`).
Gracefully disabled when not available.
"""

import asyncio
import logging
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import cancel_task

logger = logging.getLogger(__name__)

try:
    from systemd import journal  # type: ignore[import-untyped]

    HAS_SYSTEMD = True
except ImportError:
    HAS_SYSTEMD = False

# Leading \b ensures word start; no trailing \b so "fail" matches "failed", "failure" etc.
PREFILTER_RE = re.compile(
    r"(?i)\b(error|critical|fail|denied|oom|segfault|killed|panic|"
    r"coredump|timeout|refused|unreachable|dropped|degraded|"
    r"emergency|alert|fatal|abort|corrupt)"
)

BATCH_SECONDS = 5
BUFFER_MAXLEN = 5000
FLUSH_YIELD_EVERY = 100  # yield to event loop every N publishes

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
    message: str
    unit: str
    priority: int
    timestamp: datetime


def priority_to_severity(priority: int) -> Severity:
    return _PRIORITY_MAP.get(priority, Severity.INFO)


def matches_prefilter(message: str) -> bool:
    return PREFILTER_RE.search(message) is not None


class JournaldSource:
    """Journald event source with regex pre-filter and batched dispatch."""

    name = "journald"

    def __init__(self, batch_seconds: float = BATCH_SECONDS) -> None:
        if batch_seconds <= 0:
            raise ValueError("batch_seconds must be positive")
        self._batch_seconds = batch_seconds
        self._bus: EventBus | None = None
        self._buffer: deque[JournalEntry] = deque(maxlen=BUFFER_MAXLEN)
        self._reader_task: asyncio.Task[None] | None = None
        self._batcher_task: asyncio.Task[None] | None = None
        self._stop_event = threading.Event()

    @property
    def bus(self) -> EventBus:
        if self._bus is None:
            raise RuntimeError(f"Source '{self.name}' not started — call start() first")
        return self._bus

    async def start(self, bus: EventBus) -> None:
        if not HAS_SYSTEMD:
            logger.warning("python-systemd not installed, journald source disabled")
            return

        self._bus = bus
        self._stop_event.clear()
        self._reader_task = asyncio.create_task(self._run_reader())
        self._batcher_task = asyncio.create_task(self._run_batcher())

    async def stop(self) -> None:
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
                        if not msg or not matches_prefilter(msg):
                            continue

                        ts = entry.get("__REALTIME_TIMESTAMP")
                        if ts is None:
                            ts = datetime.now(timezone.utc)
                        elif ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)

                        je = JournalEntry(
                            message=msg,
                            unit=entry.get("_SYSTEMD_UNIT", "unknown"),
                            priority=int(entry.get("PRIORITY", 6)),
                            timestamp=ts,
                        )
                        self._buffer.append(je)
        except Exception:
            logger.exception("Journal reader loop error")
        finally:
            reader.close()

    async def _run_batcher(self) -> None:
        """Periodically flush buffered entries as SystemEvents."""
        while True:
            await asyncio.sleep(self._batch_seconds)
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

        for i, entry in enumerate(entries):
            event = SystemEvent(
                type=EventType.LOG_ANOMALY,
                severity=priority_to_severity(entry.priority),
                raw_data=f"unit={entry.unit} priority={entry.priority} msg={entry.message}",
                source=self.name,
                summary=None,  # LLM classification fills this later
                timestamp=entry.timestamp,
            )
            await self.bus.publish(event)
            # Yield to event loop periodically — prevents bus queue overflow
            if i % FLUSH_YIELD_EVERY == FLUSH_YIELD_EVERY - 1:
                await asyncio.sleep(0)
