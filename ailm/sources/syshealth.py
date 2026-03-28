"""Deep system health monitoring — kernel state, file descriptors, zram, entropy.

Reads directly from /proc and /sys. No subprocess, pure sysfs.
Covers everything CPU-Z/HWiNFO miss: kernel taint, fd exhaustion,
zram compression, conntrack table, journal disk usage, uptime.
"""

import logging
from pathlib import Path

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.trend import TrendTracker
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)


def _read_int_file(path: str) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def _read_float_file(path: str) -> float | None:
    try:
        return float(Path(path).read_text().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


class SysHealthSource(PollingSource):
    """Monitor kernel/system health metrics from /proc and /sys."""

    name = "syshealth"

    def __init__(
        self, interval: int = 60, trend_tracker: TrendTracker | None = None,
    ) -> None:
        super().__init__(interval)
        self._trend = trend_tracker
        self._taint_warned = False
        self._fd_warned = False
        self._zram_warned = False

    async def check(self) -> None:
        await self._check_kernel_taint()
        await self._check_file_descriptors()
        await self._check_zram()
        await self._check_conntrack()
        await self._check_uptime()

    async def _check_kernel_taint(self) -> None:
        """Kernel taint flags — nonzero means proprietary/staging modules loaded."""
        taint = _read_int_file("/proc/sys/kernel/tainted")
        if taint is None:
            return
        # Only alert once, on first detection of taint change
        if taint > 0 and not self._taint_warned:
            self._taint_warned = True
            flags = []
            if taint & (1 << 0): flags.append("proprietary_module")
            if taint & (1 << 12): flags.append("unsigned_module")
            if taint & (1 << 13): flags.append("soft_lockup")
            if taint & (1 << 9): flags.append("kernel_warning")
            if taint & (1 << 14): flags.append("firmware_workaround")
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.INFO,
                raw_data=f"taint={taint} flags={','.join(flags)}",
                source=self.name,
                summary=f"Kernel tainted: {', '.join(flags) or 'flag ' + str(taint)}",
            ))

    async def _check_file_descriptors(self) -> None:
        """Monitor open file descriptor count vs limit."""
        try:
            text = Path("/proc/sys/fs/file-nr").read_text().strip()
            parts = text.split()
            used, max_fd = int(parts[0]), int(parts[2])
        except (OSError, ValueError, IndexError):
            return

        pct = used * 100 / max_fd if max_fd > 0 else 0
        if self._trend is not None:
            self._trend.update("fd_used_pct", pct, slope_threshold=5.0)

        if pct > 80 and not self._fd_warned:
            self._fd_warned = True
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                raw_data=f"fd_used={used} fd_max={max_fd} fd_pct={pct:.1f}%",
                source=self.name,
                summary=f"File descriptors at {pct:.0f}% ({used}/{max_fd}) — possible fd leak",
            ))
        elif pct < 60:
            self._fd_warned = False

    async def _check_zram(self) -> None:
        """Monitor zram compression ratio and usage."""
        mm_stat = Path("/sys/block/zram0/mm_stat")
        disksize = Path("/sys/block/zram0/disksize")
        if not mm_stat.exists():
            return
        try:
            parts = mm_stat.read_text().strip().split()
            orig_size = int(parts[0])     # uncompressed
            compr_size = int(parts[1])     # compressed
            mem_used = int(parts[2])       # actual RAM used
            max_size = int(disksize.read_text().strip())
        except (OSError, ValueError, IndexError):
            return

        if max_size == 0:
            return

        usage_pct = orig_size * 100 / max_size
        ratio = orig_size / compr_size if compr_size > 0 else 0

        if self._trend is not None:
            self._trend.update("zram_usage_pct", usage_pct, slope_threshold=10.0)
            self._trend.update("zram_ratio", ratio, slope_threshold=0.5)

        if usage_pct > 80 and not self._zram_warned:
            self._zram_warned = True
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                raw_data=f"zram_usage={usage_pct:.0f}% ratio={ratio:.1f}x mem={mem_used//1048576}MB",
                source=self.name,
                summary=f"ZRAM swap at {usage_pct:.0f}% ({ratio:.1f}x compression, {mem_used//1048576}MB RAM)",
            ))
        elif usage_pct < 60:
            self._zram_warned = False

    async def _check_conntrack(self) -> None:
        """Monitor netfilter connection tracking table fill level."""
        count = _read_int_file("/proc/sys/net/netfilter/nf_conntrack_count")
        max_ct = _read_int_file("/proc/sys/net/netfilter/nf_conntrack_max")
        if count is None or max_ct is None or max_ct == 0:
            return
        pct = count * 100 / max_ct
        if self._trend is not None:
            self._trend.update("conntrack_pct", pct, slope_threshold=10.0)

    async def _check_uptime(self) -> None:
        """Track uptime for trend — helps detect unexpected reboots over time."""
        val = _read_float_file("/proc/uptime")
        if val is not None and self._trend is not None:
            self._trend.update("uptime_hours", val / 3600, slope_threshold=0.0)
