"""Disk usage monitor — publishes DISK_ALERT on threshold crossing."""

import psutil

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource


class DiskMonitor(PollingSource):
    name = "disk"

    def __init__(self, warn_pct: int, critical_pct: int, interval: int, path: str = "/") -> None:
        super().__init__(interval)
        self._warn_pct = warn_pct
        self._critical_pct = critical_pct
        self._path = path
        self._last_severity: Severity | None = None

    async def check(self) -> None:
        usage = psutil.disk_usage(self._path)
        pct = usage.percent

        if pct >= self._critical_pct:
            severity = Severity.CRITICAL
        elif pct >= self._warn_pct:
            severity = Severity.WARNING
        else:
            self._last_severity = None
            return

        if severity == self._last_severity:
            return

        self._last_severity = severity
        event = SystemEvent(
            type=EventType.DISK_ALERT,
            severity=severity,
            raw_data=f"percent={pct} total={usage.total} used={usage.used} free={usage.free}",
            source=self.name,
            summary=f"Disk usage at {pct:.0f}% on {self._path}",
        )
        await self.bus.publish(event)
