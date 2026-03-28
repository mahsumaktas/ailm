"""Orphan package detection via pacman.

Uses create_subprocess_exec with fixed flags only.
No shell invocation, no user input interpolation.
"""

import asyncio
import logging

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)


class OrphanSource(PollingSource):
    """Daily scan for orphan packages. create_subprocess_exec only."""

    name = "orphan"

    def __init__(self, interval: int = 86400) -> None:
        super().__init__(interval)
        self._last_count: int | None = None

    async def check(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pacman", "-Qtd",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError):
            return

        lines = [line.strip() for line in stdout.decode().splitlines() if line.strip()]
        count = len(lines)

        if self._last_count is not None and count > self._last_count:
            new = count - self._last_count
            sample = ", ".join(line.split()[0] for line in lines[:5])
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.INFO,
                raw_data=f"orphan_count={count} new={new} packages={sample}",
                source=self.name,
                summary=f"{count} orphan packages ({new} new): {sample}",
            ))
        elif self._last_count is None and count > 0:
            logger.info("Orphan packages: %d", count)

        self._last_count = count
