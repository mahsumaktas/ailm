"""Disk monitor tests with mocked psutil."""

from collections import namedtuple
from unittest.mock import patch

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.disk import DiskMonitor

DiskUsage = namedtuple("DiskUsage", ["total", "used", "free", "percent"])


def _usage(pct: float) -> DiskUsage:
    total = 500_000_000_000
    used = int(total * pct / 100)
    return DiskUsage(total=total, used=used, free=total - used, percent=pct)


class TestDiskCheck:
    async def test_below_threshold_no_event(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(50.0)):
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 0

    async def test_warning_threshold(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(85.0)):
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 1
        assert events[0].type == EventType.DISK_ALERT
        assert events[0].severity == Severity.WARNING
        assert "85%" in events[0].summary

    async def test_critical_threshold(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(97.0)):
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 1
        assert events[0].severity == Severity.CRITICAL

    async def test_exact_warn_boundary(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(80.0)):
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 1
        assert events[0].severity == Severity.WARNING


class TestDiskDedup:
    async def test_same_severity_not_repeated(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(85.0)):
            await monitor.check()
            await monitor.check()
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 1

    async def test_severity_escalation_publishes(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(85.0)):
            await monitor.check()

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(97.0)):
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 2
        assert events[0].severity == Severity.WARNING
        assert events[1].severity == Severity.CRITICAL

    async def test_recovery_then_re_alert(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(85.0)):
            await monitor.check()

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(70.0)):
            await monitor.check()

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(85.0)):
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert len(events) == 2


class TestDiskRawData:
    async def test_raw_data_contains_metrics(self, bus, events):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        await bus.start()
        await monitor.start(bus)

        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(90.0)):
            await monitor.check()

        await monitor.stop()
        await bus.stop()
        assert "percent=90" in events[0].raw_data
        assert "total=" in events[0].raw_data
        assert events[0].source == "disk"


class TestDiskLifecycle:
    async def test_check_before_start_raises(self):
        monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
        with patch("ailm.sources.disk.psutil.disk_usage", return_value=_usage(90.0)):
            with pytest.raises(RuntimeError, match="not started"):
                await monitor.check()
