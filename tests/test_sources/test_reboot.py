"""Reboot source tests."""

from pathlib import Path

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.reboot import RebootSource


class TestRebootCheck:
    async def test_no_sentinel_no_event(self, tmp_path: Path, bus, events):
        source = RebootSource(interval=60, sentinel_path=str(tmp_path / "reboot-required"))
        await bus.start()
        await source.start(bus)
        await source.check()
        await source.stop()
        await bus.stop()
        assert len(events) == 0

    async def test_sentinel_exists_publishes(self, tmp_path: Path, bus, events):
        sentinel = tmp_path / "reboot-required"
        sentinel.touch()

        source = RebootSource(interval=60, sentinel_path=str(sentinel))
        await bus.start()
        await source.start(bus)
        await source.check()
        await source.stop()
        await bus.stop()

        assert len(events) == 1
        assert events[0].type == EventType.REBOOT_REQUIRED
        assert events[0].severity == Severity.WARNING

    async def test_sentinel_dedup(self, tmp_path: Path, bus, events):
        sentinel = tmp_path / "reboot-required"
        sentinel.touch()

        source = RebootSource(interval=60, sentinel_path=str(sentinel))
        await bus.start()
        await source.start(bus)
        await source.check()
        await source.check()
        await source.check()
        await source.stop()
        await bus.stop()

        assert len(events) == 1

    async def test_sentinel_removed_then_recreated(self, tmp_path: Path, bus, events):
        sentinel = tmp_path / "reboot-required"
        sentinel.touch()

        source = RebootSource(interval=60, sentinel_path=str(sentinel))
        await bus.start()
        await source.start(bus)

        await source.check()  # first detection
        sentinel.unlink()
        await source.check()  # cleared
        sentinel.touch()
        await source.check()  # re-detection

        await source.stop()
        await bus.stop()
        assert len(events) == 2

    async def test_check_before_start_raises(self, tmp_path: Path):
        sentinel = tmp_path / "reboot-required"
        sentinel.touch()
        source = RebootSource(interval=60, sentinel_path=str(sentinel))
        with pytest.raises(RuntimeError, match="not started"):
            await source.check()
