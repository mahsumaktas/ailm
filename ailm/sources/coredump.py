"""Coredump monitoring via coredumpctl.

Detects new application crashes by polling coredumpctl list.
All subprocess calls use create_subprocess_exec — no shell, no user input.
"""

import asyncio
import logging

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)


class CoredumpSource(PollingSource):
    """Poll coredumpctl for new crashes. create_subprocess_exec only."""

    name = "coredump"

    def __init__(self, interval: int = 60) -> None:
        super().__init__(interval)
        self._known: set[str] = set()
        self._available = False
        self._first_scan = True

    async def start(self, bus) -> None:
        self._available = await self._check()
        if not self._available:
            logger.info("coredumpctl not available")
            return
        await super().start(bus)

    async def _check(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "coredumpctl", "list", "--no-pager",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def check(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "coredumpctl", "list", "--no-pager", "--since", "1 hour ago",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError):
            return

        current: set[str] = set()
        entries: list[tuple[str, str, str]] = []  # (key, signal, exe)

        for line in stdout.decode().splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            pid = signal = exe = None
            for p in parts:
                if p.isdigit() and pid is None:
                    pid = p
                elif p.startswith("SIG") and signal is None:
                    signal = p
                elif "/" in p and pid is not None:
                    exe = p
                    break
            if pid and signal:
                key = f"{pid}_{signal}"
                current.add(key)
                if key not in self._known:
                    entries.append((key, signal, exe or "unknown"))

        # First scan: populate silently
        if self._first_scan:
            self._known = current
            self._first_scan = False
            return

        for key, signal, exe in entries:
            self._known.add(key)
            exe_short = exe.split("/")[-1]
            sev = Severity.CRITICAL if signal in ("SIGSEGV", "SIGBUS") else Severity.WARNING
            await self.bus.publish(SystemEvent(
                type=EventType.LOG_ANOMALY, severity=sev,
                raw_data=f"signal={signal} exe={exe}",
                source=self.name,
                summary=f"Crash: {exe_short} ({signal})",
            ))
