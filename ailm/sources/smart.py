"""NVMe SMART health monitoring — wear level, temperature, errors.

Uses smartctl via create_subprocess_exec with fixed device path
argument only. No shell invocation, no user input interpolation.
Gracefully disabled when smartctl unavailable or permission denied.
"""

import asyncio
import logging
import re

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_SMART_RE = {
    "temperature": re.compile(r"Temperature:\s+(\d+)\s+Celsius"),
    "spare": re.compile(r"Available Spare:\s+(\d+)%"),
    "spare_threshold": re.compile(r"Available Spare Threshold:\s+(\d+)%"),
    "used": re.compile(r"Percentage Used:\s+(\d+)%"),
    "media_errors": re.compile(r"Media and Data Integrity Errors:\s+(\d+)"),
}


class SmartSource(PollingSource):
    """Poll smartctl for NVMe health (fixed device path, no user input)."""

    name = "smart"

    def __init__(self, device: str = "/dev/nvme0", interval: int = 3600) -> None:
        super().__init__(interval)
        self._device = device
        self._available = False
        self._last_used_pct: int | None = None
        self._last_media_errors: int | None = None

    async def start(self, bus) -> None:
        self._available = await self._check_smartctl()
        if not self._available:
            logger.info("smartctl not available for %s, SMART source disabled", self._device)
            return
        await super().start(bus)

    async def _check_smartctl(self) -> bool:
        """Check smartctl access (hardcoded args, safe)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "smartctl", "-i", self._device,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return b"NVMe" in stdout or b"Model" in stdout
        except (OSError, asyncio.TimeoutError):
            return False

    async def check(self) -> None:
        if not self._available:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "smartctl", "-a", self._device,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError):
            return

        text = stdout.decode()
        values: dict[str, str] = {}
        for key, pattern in _SMART_RE.items():
            m = pattern.search(text)
            if m:
                values[key] = m.group(1)

        used_pct = int(values.get("used", "0"))
        spare_pct = int(values.get("spare", "100"))
        spare_thresh = int(values.get("spare_threshold", "5"))
        media_errors = int(values.get("media_errors", "0"))
        temp = int(values.get("temperature", "0"))

        raw = f"used={used_pct}% spare={spare_pct}% media_errors={media_errors} temp={temp}C"

        # Wear level warning
        if used_pct >= 80 and (self._last_used_pct is None or self._last_used_pct < 80):
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                raw_data=raw, source=self.name,
                summary=f"NVMe wear level at {used_pct}% — plan replacement",
            ))

        # Spare below threshold
        if spare_pct <= spare_thresh:
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.CRITICAL,
                raw_data=raw, source=self.name,
                summary=f"NVMe spare at {spare_pct}% (threshold {spare_thresh}%) — drive failing",
            ))

        # New media errors
        if self._last_media_errors is not None and media_errors > self._last_media_errors:
            new = media_errors - self._last_media_errors
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.CRITICAL,
                raw_data=raw, source=self.name,
                summary=f"NVMe: {new} new media errors (total {media_errors})",
            ))

        # NVMe temperature warning
        if temp >= 70:
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                raw_data=raw, source=self.name,
                summary=f"NVMe temperature at {temp}C — throttling likely",
            ))

        self._last_used_pct = used_pct
        self._last_media_errors = media_errors
