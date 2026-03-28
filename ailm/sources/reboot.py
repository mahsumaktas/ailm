"""Reboot-required monitor — checks running vs installed kernel on Arch/CachyOS."""

import logging
import platform
from pathlib import Path

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)


def _check_kernel_mismatch() -> str | None:
    """Compare running kernel with installed. Returns mismatch description or None."""
    running = platform.release()  # e.g. "6.18.19-2-cachyos-lts"
    # Check /usr/lib/modules for installed kernels
    modules_dir = Path("/usr/lib/modules")
    if not modules_dir.is_dir():
        return None
    installed = {d.name for d in modules_dir.iterdir() if d.is_dir()}
    if running not in installed:
        return f"running={running} installed={','.join(sorted(installed))}"
    return None


class RebootSource(PollingSource):
    """Poll reboot-required signals and emit warnings when a reboot is needed."""

    name = "reboot"

    def __init__(self, interval: int = 300,
                 sentinel_path: str = "/run/reboot-required") -> None:
        super().__init__(interval)
        self._sentinel = Path(sentinel_path)
        self._was_required = False

    async def check(self) -> None:
        """Publish a reboot-required event when the requirement first appears."""
        # Check 1: sentinel file (Debian/Ubuntu convention, cachyos-reboot-required)
        sentinel_exists = self._sentinel.exists()

        # Check 2: kernel version mismatch (Arch/CachyOS native check)
        kernel_mismatch = _check_kernel_mismatch()

        is_required = sentinel_exists or kernel_mismatch is not None

        if is_required and not self._was_required:
            reason = f"sentinel={self._sentinel}" if sentinel_exists else f"kernel_mismatch: {kernel_mismatch}"
            event = SystemEvent(
                type=EventType.REBOOT_REQUIRED,
                severity=Severity.WARNING,
                raw_data=reason,
                source=self.name,
                summary="System reboot required",
            )
            await self.bus.publish(event)

        self._was_required = is_required
