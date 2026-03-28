"""Pacnew file detector — finds unmerged .pacnew config files in /etc."""

import asyncio
import logging

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_DIFF_MAX_LINES = 50


class PacnewSource(PollingSource):
    """Hourly scan of /etc for .pacnew files, emit CONFIG_CHANGE events."""

    name = "pacnew"

    def __init__(self, interval: int = 3600) -> None:
        super().__init__(interval)
        self._known: set[str] = set()
        self._first_scan = True

    async def check(self) -> None:
        """Scan for .pacnew files and publish events for new ones."""
        current = await self._find_pacnew()

        new_files = current - self._known
        merged = self._known - current

        if self._first_scan:
            # First scan: populate silently (don't alert for pre-existing files)
            self._known = current
            self._first_scan = False
            if current:
                logger.info("Initial .pacnew scan: %d existing files", len(current))
            return

        for path in sorted(new_files):
            diff = await self._get_diff(path)
            event = SystemEvent(
                type=EventType.CONFIG_CHANGE,
                severity=Severity.WARNING,
                raw_data=f"path={path} diff_preview={diff[:1000]}",
                source=self.name,
                summary=f"New .pacnew: {path} — config merge needed",
            )
            await self.bus.publish(event)

        for path in sorted(merged):
            logger.info("Pacnew merged or removed: %s", path)

        self._known = current

    async def _find_pacnew(self) -> set[str]:
        """Run find /etc -name '*.pacnew' and return paths.

        Uses create_subprocess_exec with fixed arguments (no user input).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "find", "/etc", "-name", "*.pacnew", "-type", "f",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            return {line.strip() for line in stdout.decode().splitlines() if line.strip()}
        except (OSError, asyncio.TimeoutError):
            logger.warning("Failed to scan for .pacnew files")
            return self._known  # keep previous state on error

    async def _get_diff(self, pacnew_path: str) -> str:
        """Get unified diff between original config and .pacnew file.

        Uses create_subprocess_exec with hardcoded diff flags (safe).
        """
        original = pacnew_path.removesuffix(".pacnew")
        try:
            proc = await asyncio.create_subprocess_exec(
                "diff", "-u", original, pacnew_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            lines = stdout.decode().splitlines()
            return "\n".join(lines[:_DIFF_MAX_LINES])
        except (OSError, asyncio.TimeoutError):
            return "(diff unavailable)"
