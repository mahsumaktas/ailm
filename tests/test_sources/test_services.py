"""Service monitor tests with mocked SystemdInit."""

from unittest.mock import AsyncMock

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.services import ServiceMonitor


def _make_monitor(failed_units: list[str] | None = None) -> ServiceMonitor:
    """Create a ServiceMonitor with a mocked InitSystem."""
    mock_init = AsyncMock()
    mock_init.get_failed_units.return_value = failed_units or []
    return ServiceMonitor(interval=60, init_system=mock_init)


class TestServiceCheck:
    async def test_no_failures(self, bus, events):
        monitor = _make_monitor([])
        await bus.start()
        await monitor.start(bus)
        await monitor.check()
        await monitor.stop()
        await bus.stop()
        assert len(events) == 0

    async def test_single_failure(self, bus, events):
        monitor = _make_monitor(["bluetooth.service"])
        await bus.start()
        await monitor.start(bus)
        await monitor.check()
        await monitor.stop()
        await bus.stop()
        assert len(events) == 1
        assert events[0].type == EventType.SERVICE_FAIL
        assert events[0].severity == Severity.CRITICAL
        assert "bluetooth.service" in events[0].summary

    async def test_multiple_failures(self, bus, events):
        monitor = _make_monitor(["bluetooth.service", "cups.service"])
        await bus.start()
        await monitor.start(bus)
        await monitor.check()
        await monitor.stop()
        await bus.stop()
        assert len(events) == 2
        units = {e.raw_data.split("=")[1].split()[0] for e in events}
        assert units == {"bluetooth.service", "cups.service"}


class TestServiceDedup:
    async def test_known_failure_not_repeated(self, bus, events):
        monitor = _make_monitor(["bluetooth.service"])
        await bus.start()
        await monitor.start(bus)
        await monitor.check()
        await monitor.check()
        await monitor.check()
        await monitor.stop()
        await bus.stop()
        assert len(events) == 1

    async def test_new_failure_after_initial(self, bus, events):
        mock_init = AsyncMock()
        mock_init.get_failed_units.side_effect = [
            ["bluetooth.service"],
            ["bluetooth.service", "cups.service"],
        ]
        monitor = ServiceMonitor(interval=60, init_system=mock_init)
        await bus.start()
        await monitor.start(bus)

        await monitor.check()
        await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 2
        assert "cups.service" in events[1].summary

    async def test_recovery_clears_known(self, bus, events):
        mock_init = AsyncMock()
        mock_init.get_failed_units.side_effect = [
            ["bluetooth.service"],
            [],
            ["bluetooth.service"],
        ]
        monitor = ServiceMonitor(interval=60, init_system=mock_init)
        await bus.start()
        await monitor.start(bus)

        await monitor.check()
        await monitor.check()
        await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 2


class TestServiceNaming:
    async def test_source_field_equals_name(self, bus, events):
        monitor = _make_monitor(["test.service"])
        await bus.start()
        await monitor.start(bus)
        await monitor.check()
        await monitor.stop()
        await bus.stop()
        assert events[0].source == "services"


class TestServiceLifecycle:
    async def test_check_before_start_raises(self):
        monitor = _make_monitor(["bluetooth.service"])
        with pytest.raises(RuntimeError, match="not started"):
            await monitor.check()
