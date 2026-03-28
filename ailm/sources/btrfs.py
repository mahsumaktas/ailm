"""Btrfs filesystem health monitoring.

All subprocess calls use create_subprocess_exec with fixed arguments.
No shell invocation, no user input interpolation.
"""

import asyncio
import logging
import re

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_STAT_RE = re.compile(r"\[(/dev/\S+)\]\.(\w+)\s+(\d+)")


class BtrfsSource(PollingSource):
    """Poll btrfs device stats and usage. create_subprocess_exec only."""

    name = "btrfs"

    def __init__(self, mountpoint: str = "/", interval: int = 300) -> None:
        super().__init__(interval)
        self._mountpoint = mountpoint
        self._available = False
        self._prev_stats: dict[str, int] = {}
        self._usage_warned = False

    async def start(self, bus) -> None:
        self._available = await self._check()
        if not self._available:
            logger.info("btrfs not detected on %s", self._mountpoint)
            return
        await super().start(bus)

    async def _check(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "btrfs", "device", "stats", self._mountpoint,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            return await asyncio.wait_for(proc.wait(), timeout=10) == 0
        except (OSError, asyncio.TimeoutError):
            return False

    async def check(self) -> None:
        await self._check_device_stats()
        await self._check_usage()

    async def _check_device_stats(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "btrfs", "device", "stats", self._mountpoint,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (OSError, asyncio.TimeoutError):
            return

        for m in _STAT_RE.finditer(stdout.decode()):
            device, stat_name, value = m.group(1), m.group(2), int(m.group(3))
            key = f"{device}.{stat_name}"
            prev = self._prev_stats.get(key, 0)
            if value > prev and prev > 0:
                new = value - prev
                sev = Severity.CRITICAL if "corruption" in stat_name else Severity.WARNING
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=sev,
                    raw_data=f"device={device} stat={stat_name} count={value} new={new}",
                    source=self.name,
                    summary=f"btrfs {device}: {new} new {stat_name} (total {value})",
                ))
            self._prev_stats[key] = value

    async def _check_usage(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "btrfs", "fi", "usage", "-b", self._mountpoint,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (OSError, asyncio.TimeoutError):
            return

        for line in stdout.decode().splitlines():
            if "Free (estimated)" in line:
                for p in line.split():
                    try:
                        free_gb = int(p) / (1024**3)
                        if free_gb < 50 and not self._usage_warned:
                            self._usage_warned = True
                            await self.bus.publish(SystemEvent(
                                type=EventType.DISK_ALERT, severity=Severity.WARNING,
                                raw_data=f"btrfs_free_gb={free_gb:.1f}",
                                source=self.name,
                                summary=f"btrfs {self._mountpoint}: only {free_gb:.1f} GB free",
                            ))
                        elif free_gb >= 100:
                            self._usage_warned = False
                        break
                    except ValueError:
                        continue
