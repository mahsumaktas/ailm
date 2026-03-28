"""Application orchestrator tests."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ailm.config.schema import AilmConfig
from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_config(tmp_path: Path) -> AilmConfig:
    """Config with DB in tmp_path and LLM disabled."""
    return AilmConfig(
        db={"path": str(tmp_path / "test.db")},
        llm={"enabled": False},
        sources={
            "journald_enabled": False,
            "snapshot_path": str(tmp_path / "nonexistent_snapshots"),
        },
    )


@pytest.fixture
def app_config_llm_enabled(tmp_path: Path) -> AilmConfig:
    """Config with LLM enabled."""
    return AilmConfig(
        db={"path": str(tmp_path / "test.db")},
        llm={"enabled": True},
        sources={
            "journald_enabled": False,
            "snapshot_path": str(tmp_path / "nonexistent_snapshots"),
        },
    )


@pytest.fixture
def app_config_journald(tmp_path: Path) -> AilmConfig:
    """Config with journald enabled."""
    return AilmConfig(
        db={"path": str(tmp_path / "test.db")},
        llm={"enabled": False},
        sources={
            "journald_enabled": True,
            "snapshot_path": str(tmp_path / "nonexistent_snapshots"),
        },
    )


@pytest.fixture
def app_config_snapshots(tmp_path: Path) -> AilmConfig:
    """Config with existing snapshot path."""
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    return AilmConfig(
        db={"path": str(tmp_path / "test.db")},
        llm={"enabled": False},
        sources={
            "journald_enabled": False,
            "snapshot_path": str(snap_dir),
        },
    )


def _make_mock_source(name: str = "mock"):
    """Create a mock source with async start/stop."""
    source = MagicMock()
    source.name = name
    source.start = AsyncMock()
    source.stop = AsyncMock()
    return source


# ---------------------------------------------------------------------------
# Import test
# ---------------------------------------------------------------------------

class TestImports:
    """Verify Application can be imported cleanly."""

    def test_import_application(self):
        from ailm.app import Application
        assert Application is not None

    def test_headless_no_qt_import(self):
        """run_headless should not import PySide6."""
        # If PySide6 was already imported, skip this test
        if "PySide6" in sys.modules:
            pytest.skip("PySide6 already imported in this process")

        from ailm.__main__ import run_headless
        assert run_headless is not None
        # run_headless imports Application lazily, no Qt
        assert "PySide6.QtWidgets" not in sys.modules


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

class TestApplicationLifecycle:
    """Start/stop lifecycle tests."""

    @pytest.mark.asyncio
    async def test_start_stop(self, app_config):
        """Application starts and stops cleanly with mocked sources."""
        from ailm.app import Application

        app = Application(app_config)

        # Replace _register_sources to use mocks
        mock_src = _make_mock_source("test_src")
        app._register_sources = lambda: app.sources.append(mock_src)

        # Mock scheduler to avoid APScheduler complexity
        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()

            # Verify subsystems are up
            assert app.db is not None
            assert app.repo is not None
            assert app.bus.running
            assert mock_src.start.called
            assert sched_inst.start.called

            await app.stop()

            # Verify subsystems are down
            assert app.db is None
            assert app.repo is None
            assert not app.bus.running
            assert mock_src.stop.called
            assert sched_inst.stop.called

    @pytest.mark.asyncio
    async def test_start_connects_db(self, app_config):
        """DB is connected after start."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()
            assert app.db is not None
            assert app.db._conn is not None
            await app.stop()

    @pytest.mark.asyncio
    async def test_llm_not_created_when_disabled(self, app_config):
        """LLM client is None when config.llm.enabled is False."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()
            assert app.llm is None
            await app.stop()

    @pytest.mark.asyncio
    async def test_llm_created_when_enabled(self, app_config_llm_enabled):
        """LLM client is created when config.llm.enabled is True."""
        from ailm.app import Application

        app = Application(app_config_llm_enabled)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched, \
             patch("ailm.app.OllamaClient") as MockLLM:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            llm_inst = MockLLM.return_value
            llm_inst.start = AsyncMock()
            llm_inst.close = AsyncMock()
            llm_inst.available = False

            await app.start()
            assert app.llm is not None
            assert llm_inst.start.called
            await app.stop()
            assert llm_inst.close.called

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, app_config):
        """Stopping without starting doesn't raise."""
        from ailm.app import Application

        app = Application(app_config)
        # Should not raise
        await app.stop()

    @pytest.mark.asyncio
    async def test_double_stop(self, app_config):
        """Stopping twice doesn't raise."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()
            await app.stop()
            await app.stop()  # second stop should be safe


# ---------------------------------------------------------------------------
# Source registration
# ---------------------------------------------------------------------------

class TestSourceRegistration:
    """Verify sources are registered based on config."""

    def test_default_sources_registered(self, app_config):
        """Default config registers disk, services, pacman, reboot."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources()

        names = [s.name for s in app.sources]
        assert "disk" in names
        assert "services" in names
        assert "pacman" in names
        assert "reboot" in names

    def test_journald_disabled(self, app_config):
        """journald_enabled=False excludes JournaldSource."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources()

        names = [s.name for s in app.sources]
        assert "journald" not in names

    def test_journald_enabled(self, app_config_journald):
        """journald_enabled=True includes JournaldSource."""
        from ailm.app import Application

        app = Application(app_config_journald)
        app._register_sources()

        names = [s.name for s in app.sources]
        assert "journald" in names

    def test_snapshot_excluded_when_path_missing(self, app_config):
        """Snapshot source excluded when snapshot_path doesn't exist."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources()

        names = [s.name for s in app.sources]
        assert "snapshot" not in names

    def test_snapshot_included_when_path_exists(self, app_config_snapshots):
        """Snapshot source included when snapshot_path exists."""
        from ailm.app import Application

        app = Application(app_config_snapshots)
        app._register_sources()

        names = [s.name for s in app.sources]
        assert "snapshot" in names

    def test_source_count_minimal(self, app_config):
        """With journald off and no snapshots: 5 sources (includes pacnew)."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources()
        assert len(app.sources) == 5  # disk, services, pacman, reboot, pacnew

    def test_source_count_all(self, app_config_snapshots):
        """With snapshots dir and journald: 7 sources."""
        from ailm.app import Application

        # Enable journald too
        app_config_snapshots.sources.journald_enabled = True
        app = Application(app_config_snapshots)
        app._register_sources()
        assert len(app.sources) == 7  # disk, services, pacman, reboot, snapshot, pacnew, journald


# ---------------------------------------------------------------------------
# Event persistence (bus -> DB subscriber)
# ---------------------------------------------------------------------------

class TestEventPersistence:
    """Verify events flowing through bus get persisted to DB."""

    @pytest.mark.asyncio
    async def test_bus_event_persisted_to_db(self, app_config):
        """Events published on bus are stored in DB via subscriber."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()

            # Publish an event
            event = SystemEvent(
                type=EventType.DISK_ALERT,
                severity=Severity.WARNING,
                raw_data="test raw data",
                source="test",
                summary="Test disk alert",
            )
            await app.bus.publish(event)
            # Let the bus dispatch
            await asyncio.sleep(0.05)

            # Verify it's in DB
            events = await app.repo.get_recent_events(limit=10)
            assert len(events) == 1
            assert events[0].type == EventType.DISK_ALERT
            assert events[0].summary == "Test disk alert"

            await app.stop()

    @pytest.mark.asyncio
    async def test_multiple_events_persisted(self, app_config):
        """Multiple events all get persisted."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()

            for i in range(5):
                event = SystemEvent(
                    type=EventType.SYSTEM_METRIC,
                    severity=Severity.INFO,
                    raw_data=f"metric_{i}",
                    source="test",
                )
                await app.bus.publish(event)

            await asyncio.sleep(0.1)

            events = await app.repo.get_recent_events(limit=10)
            assert len(events) == 5

            await app.stop()

    @pytest.mark.asyncio
    async def test_persist_survives_db_error(self, app_config):
        """If DB insert fails, the bus continues without crashing."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()

            # Make insert fail
            app.repo.insert_event = AsyncMock(side_effect=RuntimeError("DB error"))

            event = SystemEvent(
                type=EventType.DISK_ALERT,
                severity=Severity.WARNING,
                raw_data="data",
                source="test",
            )
            await app.bus.publish(event)
            await asyncio.sleep(0.05)

            # Bus should still be running
            assert app.bus.running

            await app.stop()


# ---------------------------------------------------------------------------
# Shutdown order
# ---------------------------------------------------------------------------

class TestShutdownOrder:
    """Verify components are shut down in correct reverse order."""

    @pytest.mark.asyncio
    async def test_shutdown_order(self, app_config):
        """Shutdown proceeds: hooks -> scheduler -> sources -> llm -> bus -> db."""
        from ailm.app import Application

        app = Application(app_config)

        shutdown_order: list[str] = []

        # Mock sources
        mock_src = _make_mock_source("test_src")
        mock_src.stop = AsyncMock(side_effect=lambda: shutdown_order.append("source"))
        app._register_sources = lambda: app.sources.append(mock_src)

        # Mock hooks
        original_fire_shutdown = app.hooks.fire_shutdown
        app.hooks.fire_shutdown = lambda: (shutdown_order.append("hooks"), original_fire_shutdown())

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock(
                side_effect=lambda: shutdown_order.append("scheduler")
            )
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            # Patch bus.stop to track order
            original_bus_stop = app.bus.stop

            async def tracked_bus_stop():
                shutdown_order.append("bus")
                await original_bus_stop()

            await app.start()

            app.bus.stop = tracked_bus_stop

            # Patch db.close to track order
            original_db_close = app.db.close

            async def tracked_db_close():
                shutdown_order.append("db")
                await original_db_close()

            app.db.close = tracked_db_close

            await app.stop()

        assert shutdown_order == ["hooks", "scheduler", "source", "bus", "db"]

    @pytest.mark.asyncio
    async def test_sources_stopped_in_reverse_order(self, app_config):
        """Sources are stopped in reverse registration order."""
        from ailm.app import Application

        app = Application(app_config)

        stop_order: list[str] = []
        src1 = _make_mock_source("first")
        src1.stop = AsyncMock(side_effect=lambda: stop_order.append("first"))
        src2 = _make_mock_source("second")
        src2.stop = AsyncMock(side_effect=lambda: stop_order.append("second"))
        src3 = _make_mock_source("third")
        src3.stop = AsyncMock(side_effect=lambda: stop_order.append("third"))

        app._register_sources = lambda: app.sources.extend([src1, src2, src3])

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()
            await app.stop()

        assert stop_order == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_sources_cleared_after_stop(self, app_config):
        """Sources list is empty after stop."""
        from ailm.app import Application

        app = Application(app_config)
        mock_src = _make_mock_source("test")
        app._register_sources = lambda: app.sources.append(mock_src)

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()
            assert len(app.sources) == 1
            await app.stop()
            assert len(app.sources) == 0


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

class TestSchedulerSetup:
    """Verify scheduler jobs are configured."""

    @pytest.mark.asyncio
    async def test_scheduler_jobs_added(self, app_config):
        """Briefing and cleanup cron jobs are registered."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()

            # Two cron jobs: morning_briefing + db_cleanup
            assert sched_inst.add_cron_job.call_count == 2

            call_ids = [
                call.kwargs.get("job_id") or call.args[2]
                for call in sched_inst.add_cron_job.call_args_list
            ]
            assert "morning_briefing" in call_ids
            assert "db_cleanup" in call_ids

            await app.stop()

    @pytest.mark.asyncio
    async def test_briefing_cron_from_config(self, app_config):
        """Briefing cron expression comes from config."""
        from ailm.app import Application

        app_config.scheduler.briefing_cron = "30 7 * * *"
        app = Application(app_config)
        app._register_sources = lambda: None

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()

            briefing_call = next(
                c for c in sched_inst.add_cron_job.call_args_list
                if (c.kwargs.get("job_id") or c.args[2]) == "morning_briefing"
            )
            # Second positional arg is the cron expression
            cron_expr = briefing_call.args[1]
            assert cron_expr == "30 7 * * *"

            await app.stop()


# ---------------------------------------------------------------------------
# Constructor state
# ---------------------------------------------------------------------------

class TestConstructor:
    """Verify initial state of Application."""

    def test_initial_state(self, app_config):
        from ailm.app import Application

        app = Application(app_config)
        assert app.config is app_config
        assert isinstance(app.bus, EventBus)
        assert app.db is None
        assert app.repo is None
        assert app.llm is None
        assert app.scheduler is None
        assert app.sources == []

    def test_hooks_manager_created(self, app_config):
        from ailm.app import Application
        from ailm.hooks import HookManager

        app = Application(app_config)
        assert isinstance(app.hooks, HookManager)


# ---------------------------------------------------------------------------
# Hooks integration
# ---------------------------------------------------------------------------

class TestHooksIntegration:
    """Verify hooks are fired during lifecycle."""

    @pytest.mark.asyncio
    async def test_startup_hook_fired(self, app_config):
        """fire_startup is called during start."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        startup_called = []
        app.hooks.fire_startup = lambda: startup_called.append(True)

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()
            assert startup_called == [True]
            await app.stop()

    @pytest.mark.asyncio
    async def test_shutdown_hook_fired(self, app_config):
        """fire_shutdown is called during stop."""
        from ailm.app import Application

        app = Application(app_config)
        app._register_sources = lambda: None

        shutdown_called = []
        original = app.hooks.fire_shutdown
        app.hooks.fire_shutdown = lambda: (shutdown_called.append(True), original())

        with patch("ailm.app.SchedulerEngine") as MockSched:
            sched_inst = MockSched.return_value
            sched_inst.start = AsyncMock()
            sched_inst.stop = AsyncMock()
            sched_inst.add_cron_job = AsyncMock()
            sched_inst.add_interval_job = AsyncMock()

            await app.start()
            await app.stop()
            assert shutdown_called == [True]
