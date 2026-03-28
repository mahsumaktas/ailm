"""NVIDIA GPU monitoring — temperature, VRAM, power, Xid errors.

Uses nvidia-smi via create_subprocess_exec with fixed arguments only.
No shell invocation, no user input interpolation.
Gracefully disabled when nvidia-smi is not available.
"""

import asyncio
import logging

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.trend import TrendTracker
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_QUERY_FIELDS = "temperature.gpu,memory.used,memory.total,power.draw,pstate,clocks.gr,clocks.mem,fan.speed"
_PCIE_FIELDS = "pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max"
_VRAM_WARN_PCT = 90


class NvidiaSource(PollingSource):
    """Poll nvidia-smi for GPU metrics and publish alerts.

    All subprocess calls use create_subprocess_exec with hardcoded
    arguments — no shell invocation, no user input interpolation.
    """

    name = "nvidia"

    def __init__(
        self,
        interval: int = 30,
        trend_tracker: TrendTracker | None = None,
    ) -> None:
        super().__init__(interval)
        self._trend = trend_tracker
        self._available = False
        self._last_vram_alert = False
        self._pcie_warned = False
        self._check_count = 0

    async def start(self, bus) -> None:
        self._available = await self._check_nvidia()
        if not self._available:
            logger.info("nvidia-smi not available, GPU source disabled")
            return
        await super().start(bus)

    async def _check_nvidia(self) -> bool:
        """Probe for nvidia-smi binary (fixed args, safe)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi", "--query-gpu=name", "--format=csv,noheader",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            name = stdout.decode().strip()
            if name:
                logger.info("GPU detected: %s", name)
                return True
        except (OSError, asyncio.TimeoutError):
            pass
        return False

    async def check(self) -> None:
        if not self._available:
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                f"--query-gpu={_QUERY_FIELDS}",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (OSError, asyncio.TimeoutError):
            return

        line = stdout.decode().strip()
        if not line:
            return

        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            return

        try:
            temp = float(parts[0])
            vram_used = float(parts[1])
            vram_total = float(parts[2])
            power = float(parts[3])
            pstate = parts[4]
            fan_pct = int(parts[7].replace(" %", "").strip()) if parts[7].strip() not in ("[N/A]", "N/A", "") else 0
        except (ValueError, IndexError):
            return

        vram_pct = (vram_used / vram_total * 100) if vram_total > 0 else 0

        # Feed trends
        if self._trend is not None:
            self._trend.update("gpu_temp_c", temp, slope_threshold=5.0)
            self._trend.update("gpu_fan_pct", float(fan_pct), slope_threshold=20.0)
            self._trend.update("gpu_power_w", power, slope_threshold=30.0)
            alert = self._trend.update("gpu_vram_pct", vram_pct, slope_threshold=10.0)
            if alert is not None:
                await self.bus.publish(SystemEvent(
                    type=EventType.TREND_ALERT,
                    severity=Severity.WARNING,
                    raw_data=f"metric=gpu_vram_pct slope={alert.slope:.1f} ema={alert.ema:.1f}",
                    source=self.name,
                    summary=alert.summary,
                ))

        # VRAM threshold alert
        if vram_pct >= _VRAM_WARN_PCT and not self._last_vram_alert:
            self._last_vram_alert = True
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC,
                severity=Severity.WARNING,
                raw_data=f"gpu_temp={temp} vram_used={vram_used:.0f}MB vram_total={vram_total:.0f}MB vram_pct={vram_pct:.0f} power={power}W pstate={pstate}",
                source=self.name,
                summary=f"GPU VRAM at {vram_pct:.0f}% ({vram_used:.0f}/{vram_total:.0f} MB)",
            ))
        elif vram_pct < _VRAM_WARN_PCT:
            self._last_vram_alert = False

        # Temperature alert (>85C)
        if temp >= 85:
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC,
                severity=Severity.CRITICAL,
                raw_data=f"gpu_temp={temp} power={power}W pstate={pstate}",
                source=self.name,
                summary=f"GPU temperature critical: {temp}C (power {power}W, {pstate})",
            ))

        # PCIe link degradation check (every 10th poll = ~5min)
        self._check_count += 1
        if self._check_count % 10 == 0:
            await self._check_pcie()

    async def _check_pcie(self) -> None:
        """Check PCIe link width/gen for degradation."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                f"--query-gpu={_PCIE_FIELDS}",
                "--format=csv,noheader",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (OSError, asyncio.TimeoutError):
            return

        parts = [p.strip() for p in stdout.decode().strip().split(",")]
        if len(parts) < 4:
            return

        try:
            gen_cur, gen_max = int(parts[0]), int(parts[1])
            width_cur, width_max = int(parts[2]), int(parts[3])
        except ValueError:
            return

        if (gen_cur < gen_max or width_cur < width_max) and not self._pcie_warned:
            self._pcie_warned = True
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC,
                severity=Severity.WARNING,
                raw_data=f"pcie_gen={gen_cur}/{gen_max} pcie_width=x{width_cur}/x{width_max}",
                source=self.name,
                summary=f"GPU PCIe degraded: Gen{gen_cur} x{width_cur} (max Gen{gen_max} x{width_max})",
            ))
