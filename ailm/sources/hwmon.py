"""Hardware sensor monitoring via sysfs + /proc/diskstats.

Reads temperatures (VRM, chipset, RAM, WiFi), fan speeds, and disk I/O
directly from /sys/class/hwmon and /proc/diskstats. No subprocess needed.
"""

import logging
import time
from pathlib import Path

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.trend import TrendTracker
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_HWMON_BASE = Path("/sys/class/hwmon")
_DISKSTATS = Path("/proc/diskstats")

# Temperature thresholds per sensor type (Celsius)
_TEMP_THRESHOLDS = {
    "k10temp": 90,       # AMD CPU
    "nct6799": 85,       # VRM/chipset
    "spd5118": 55,       # RAM DIMM
    "amdgpu": 95,        # iGPU
    "nvme": 70,          # NVMe drive
    "mt7925_phy0": 85,   # WiFi
}


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_hwmon_temps() -> list[tuple[str, str, float]]:
    """Return list of (hwmon_name, label, temp_celsius)."""
    results = []
    if not _HWMON_BASE.exists():
        return results

    for hwmon_dir in _HWMON_BASE.iterdir():
        name_file = hwmon_dir / "name"
        if not name_file.exists():
            continue
        try:
            name = name_file.read_text().strip()
        except OSError:
            continue

        # Find all temp*_input files
        for temp_file in sorted(hwmon_dir.glob("temp*_input")):
            val = _read_int(temp_file)
            if val is None:
                continue
            # millidegrees → degrees
            temp_c = val / 1000.0
            # Try to get label
            label_file = temp_file.with_name(temp_file.name.replace("_input", "_label"))
            try:
                label = label_file.read_text().strip() if label_file.exists() else ""
            except OSError:
                label = ""
            results.append((name, label or temp_file.stem, temp_c))

    return results


def _read_fan_speeds() -> list[tuple[str, str, int]]:
    """Return list of (hwmon_name, label, rpm)."""
    results = []
    if not _HWMON_BASE.exists():
        return results

    for hwmon_dir in _HWMON_BASE.iterdir():
        name_file = hwmon_dir / "name"
        if not name_file.exists():
            continue
        try:
            name = name_file.read_text().strip()
        except OSError:
            continue

        for fan_file in sorted(hwmon_dir.glob("fan*_input")):
            val = _read_int(fan_file)
            if val is not None:
                label_file = fan_file.with_name(fan_file.name.replace("_input", "_label"))
                try:
                    label = label_file.read_text().strip() if label_file.exists() else fan_file.stem
                except OSError:
                    label = fan_file.stem
                results.append((name, label, val))

    return results


def _read_diskstats(device: str = "nvme0n1") -> dict | None:
    """Read /proc/diskstats for a device. Returns read/write sector counts."""
    try:
        text = _DISKSTATS.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 14 and parts[2] == device:
            return {
                "reads": int(parts[3]),
                "read_sectors": int(parts[5]),
                "writes": int(parts[7]),
                "write_sectors": int(parts[9]),
                "io_ms": int(parts[12]),
            }
    return None


class HwmonSource(PollingSource):
    """Poll hardware sensors and disk I/O stats."""

    name = "hwmon"

    def __init__(
        self, interval: int = 30, trend_tracker: TrendTracker | None = None,
    ) -> None:
        super().__init__(interval)
        self._trend = trend_tracker
        self._temp_alerted: dict[str, bool] = {}
        self._prev_diskstats: dict | None = None
        self._prev_time: float = 0.0

    async def check(self) -> None:
        await self._check_temperatures()
        await self._check_disk_io()

    async def _check_temperatures(self) -> None:
        temps = _read_hwmon_temps()
        for name, label, temp_c in temps:
            metric_name = f"temp_{name}_{label}"

            # Feed trend
            if self._trend is not None:
                self._trend.update(metric_name, temp_c, slope_threshold=5.0)

            # Threshold check
            threshold = _TEMP_THRESHOLDS.get(name, 85)
            key = f"{name}/{label}"
            was = self._temp_alerted.get(key, False)

            if temp_c >= threshold and not was:
                self._temp_alerted[key] = True
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC,
                    severity=Severity.CRITICAL if temp_c >= threshold + 10 else Severity.WARNING,
                    raw_data=f"sensor={name} label={label} temp={temp_c:.1f}C threshold={threshold}C",
                    source=self.name,
                    summary=f"{name}/{label} temperature at {temp_c:.0f}C (threshold {threshold}C)",
                ))
            elif temp_c < threshold - 5:
                self._temp_alerted[key] = False

    async def _check_disk_io(self) -> None:
        stats = _read_diskstats()
        if stats is None:
            return

        now = time.monotonic()
        if self._prev_diskstats is not None and self._prev_time > 0:
            dt = now - self._prev_time
            if dt > 0:
                # Calculate IOPS and throughput
                read_iops = (stats["reads"] - self._prev_diskstats["reads"]) / dt
                write_iops = (stats["writes"] - self._prev_diskstats["writes"]) / dt
                # Sectors are 512 bytes
                read_mbs = (stats["read_sectors"] - self._prev_diskstats["read_sectors"]) * 512 / dt / 1_000_000
                write_mbs = (stats["write_sectors"] - self._prev_diskstats["write_sectors"]) * 512 / dt / 1_000_000
                # IO utilization (ms spent doing IO / elapsed ms)
                io_util = (stats["io_ms"] - self._prev_diskstats["io_ms"]) / (dt * 1000) * 100

                if self._trend is not None:
                    self._trend.update("disk_io_util_pct", io_util, slope_threshold=20.0)
                    self._trend.update("disk_read_mbs", read_mbs, slope_threshold=50.0)
                    self._trend.update("disk_write_mbs", write_mbs, slope_threshold=50.0)

                # High IO utilization alert (>95% for sustained period)
                if io_util > 95:
                    await self.bus.publish(SystemEvent(
                        type=EventType.SYSTEM_METRIC,
                        severity=Severity.WARNING,
                        raw_data=f"io_util={io_util:.1f}% read={read_mbs:.1f}MB/s write={write_mbs:.1f}MB/s",
                        source=self.name,
                        summary=f"Disk I/O saturated: {io_util:.0f}% util ({read_mbs:.0f}MB/s read, {write_mbs:.0f}MB/s write)",
                    ))

        self._prev_diskstats = stats
        self._prev_time = now
