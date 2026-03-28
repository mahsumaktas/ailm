"""Linux PSI (Pressure Stall Information) monitoring.

Reads /proc/pressure/cpu, /proc/pressure/memory, /proc/pressure/io.
No subprocess, no external dependency — pure sysfs read.
"""

import logging
from pathlib import Path

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.trend import TrendTracker
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_PSI_PATH = Path("/proc/pressure")
# Thresholds: avg10 percentage above which we alert
_THRESHOLDS = {
    "cpu": 50.0,       # CPU pressure >50% for 10s
    "memory": 20.0,    # Memory pressure >20% for 10s
    "io": 80.0,        # I/O pressure >80% for 10s (NVMe has high baseline)
}


def _parse_psi(path: Path) -> dict[str, float] | None:
    """Parse PSI file, return avg10/avg60/avg300 for 'some' line."""
    try:
        text = path.read_text()
    except (OSError, PermissionError):
        return None
    for line in text.splitlines():
        if line.startswith("some "):
            parts = dict(kv.split("=") for kv in line.split() if "=" in kv)
            return {k: float(v) for k, v in parts.items() if k.startswith("avg")}
    return None


class PressureSource(PollingSource):
    """Monitor CPU/memory/IO pressure via /proc/pressure (PSI)."""

    name = "pressure"

    def __init__(
        self, interval: int = 30, trend_tracker: TrendTracker | None = None,
    ) -> None:
        super().__init__(interval)
        self._trend = trend_tracker
        self._available = _PSI_PATH.exists()
        self._alerted: dict[str, bool] = {}

    async def start(self, bus) -> None:
        if not self._available:
            logger.info("PSI not available (/proc/pressure missing)")
            return
        await super().start(bus)

    async def check(self) -> None:
        for resource in ("cpu", "memory", "io"):
            psi = _parse_psi(_PSI_PATH / resource)
            if psi is None:
                continue

            avg10 = psi.get("avg10", 0.0)

            # Feed trend tracker
            if self._trend is not None:
                self._trend.update(
                    f"psi_{resource}_avg10", avg10, slope_threshold=10.0,
                )

            # Threshold alert
            threshold = _THRESHOLDS.get(resource, 50.0)
            was_alert = self._alerted.get(resource, False)

            if avg10 >= threshold and not was_alert:
                self._alerted[resource] = True
                severity = Severity.CRITICAL if resource == "memory" else Severity.WARNING
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC,
                    severity=severity,
                    raw_data=f"psi_{resource} avg10={avg10:.1f} avg60={psi.get('avg60', 0):.1f}",
                    source=self.name,
                    summary=f"System {resource} pressure high: {avg10:.0f}% (10s avg)",
                ))
            elif avg10 < threshold * 0.7:
                self._alerted[resource] = False
