"""Systemd failed-unit monitor — publishes SERVICE_FAIL for new failures."""

import logging

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.distro.arch import SystemdInit
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)


class ServiceMonitor(PollingSource):
    name = "services"

    def __init__(self, interval: int = 300, init_system: SystemdInit | None = None) -> None:
        super().__init__(interval)
        self._init = init_system or SystemdInit()
        self._known_failures: set[str] = set()

    async def check(self) -> None:
        failed_list = await self._init.get_failed_units()
        failed_units = set(failed_list)

        new_failures = failed_units - self._known_failures
        recovered = self._known_failures - failed_units

        for unit in new_failures:
            event = SystemEvent(
                type=EventType.SERVICE_FAIL,
                severity=Severity.CRITICAL,
                raw_data=f"unit={unit} state=failed",
                source=self.name,
                summary=f"Service {unit} has failed",
            )
            await self.bus.publish(event)

        if recovered:
            logger.info("Services recovered: %s", ", ".join(recovered))

        self._known_failures = failed_units
