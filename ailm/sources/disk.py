"""Disk usage monitor — publishes DISK_ALERT on threshold crossing + trend."""

import psutil

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.trend import TrendTracker
from ailm.sources.base import PollingSource


class DiskMonitor(PollingSource):
    """Poll disk usage and emit alerts on threshold crossings + trend."""

    name = "disk"

    def __init__(
        self,
        warn_pct: int,
        critical_pct: int,
        interval: int,
        path: str = "/",
        trend_tracker: TrendTracker | None = None,
    ) -> None:
        super().__init__(interval)
        self._warn_pct = warn_pct
        self._critical_pct = critical_pct
        self._path = path
        self._last_severity: Severity | None = None
        self._trend = trend_tracker

    async def check(self) -> None:
        """Inspect disk usage and publish a new alert when severity changes."""
        usage = psutil.disk_usage(self._path)
        pct = usage.percent

        # Trend tracking (always, regardless of threshold)
        if self._trend is not None:
            alert = self._trend.update(
                "disk_usage_pct", pct, slope_threshold=2.0,  # 2%/hour
            )
            if alert is not None:
                await self.bus.publish(SystemEvent(
                    type=EventType.TREND_ALERT,
                    severity=Severity.WARNING,
                    raw_data=f"metric={alert.metric} slope={alert.slope:.3f} ema={alert.ema:.1f}",
                    source=self.name,
                    summary=alert.summary,
                ))

        # Threshold alerts
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
