"""Broad edge-case coverage across utility modules."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

import ailm.__main__ as cli
from ailm.config.loader import _toml_value
from ailm.config.schema import AilmConfig
from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent, SystemStatus
from ailm.core.status import STATUS_WINDOW, StatusTracker
from ailm.core.logging import setup_logging
from ailm.distro.arch import SystemdInit
from ailm.distro.protocols import PackageEvent
from ailm.hooks import HookManager, hookimpl
from ailm.llm.evidence import _extract_source, _is_header_or_empty
from ailm.llm.queue import LLMTask, LLMTaskQueue, MAX_AGE
from ailm.scheduler.engine import _cron_matches, _parse_cron
from ailm.sources.base import DEBOUNCE_SECONDS, PollingSource, WatchdogSource, cancel_task
from ailm.sources.journald import JournalEntry


def _event(
    severity: Severity,
    *,
    event_type: EventType = EventType.DISK_ALERT,
    timestamp: datetime | None = None,
) -> SystemEvent:
    """Create a simple event for status and hook tests."""
    return SystemEvent(
        type=event_type,
        severity=severity,
        raw_data="raw",
        source="edge",
        summary="summary",
        timestamp=timestamp or datetime.now(timezone.utc),
    )


@contextmanager
def _isolated_root_logging():
    """Temporarily replace root handlers so logging tests stay hermetic."""
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    try:
        yield root
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        root.handlers[:] = old_handlers
        root.setLevel(old_level)


async def _wait_for(predicate, timeout: float = 1.0) -> None:
    """Poll until a predicate becomes true."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    msg = "Timed out waiting for predicate"
    raise AssertionError(msg)


class _NoneActionPlugin:
    @hookimpl
    def on_action_requested(self, action: str, params: dict) -> None:
        return None


class _TrueActionPlugin:
    @hookimpl
    def on_action_requested(self, action: str, params: dict) -> bool:
        return True


class _FalseActionPlugin:
    @hookimpl
    def on_action_requested(self, action: str, params: dict) -> bool:
        return False


class _StartupPlugin:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    @hookimpl
    def on_startup(self) -> None:
        self.started = True

    @hookimpl
    def on_shutdown(self) -> None:
        self.stopped = True


class _FakeProc:
    """Simple async subprocess result container."""

    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


class _QueueClient:
    """Deterministic LLM client fake for queue tests."""

    def __init__(self, responses: list[str | None]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str | None]] = []

    async def generate(self, prompt: str, system: str | None = None) -> str | None:
        self.calls.append((prompt, system))
        return self._responses.pop(0)


class _DummyObserver:
    """Observer stub used by watchdog source tests."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.join_timeout: int | None = None
        self.daemon = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: int) -> None:
        self.join_timeout = timeout


class _CounterPollingSource(PollingSource):
    """Polling source that increments a counter each interval."""

    name = "counter"

    def __init__(self) -> None:
        super().__init__(interval=0.01)
        self.calls = 0

    async def check(self) -> None:
        self.calls += 1


class _DummyWatchdogSource(WatchdogSource):
    """Watchdog source with explicit test hooks."""

    name = "dummy-watchdog"

    def __init__(self) -> None:
        super().__init__()
        self.observer = _DummyObserver()
        self.calls: list[str] = []

    def _setup_observer(self):
        return self.observer

    async def record(self, value: str) -> None:
        self.calls.append(value)


class TestTomlValueEdgeCases:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, "true"),
            (False, "false"),
            (0, "0"),
            (3.5, "3.5"),
            ("plain", '"plain"'),
            ('he said "hello"', '"he said \\"hello\\""'),
            (r"C:\logs\ailm", r'"C:\\logs\\ailm"'),
            ("", '""'),
            ([1, 2, 3], "[1, 2, 3]"),
            (["a", "b"], '["a", "b"]'),
            ([True, "x"], '[true, "x"]'),
            ({"enabled": True}, "{enabled = true}"),
            ({"path": r"C:\tmp"}, r'{path = "C:\\tmp"}'),
            ({"nested": ["a", False]}, '{nested = ["a", false]}'),
            (None, "None"),
        ],
    )
    def test_toml_value_serializes_edge_cases(self, value: object, expected: str):
        assert _toml_value(value) == expected


class TestEvidenceHeaderDetection:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("", True),
            ("   ", True),
            ("# Heading", True),
            ("## Nested Heading", True),
            ("###### Deep Heading", True),
            ("---", True),
            ("====", True),
            ("***", True),
            ("***   ", True),
            ("##Heading", False),
            ("###", False),
            ("* bullet", False),
            ("- bullet", False),
            ("plain content", False),
            ("[Source: data]", False),
        ],
    )
    def test_header_or_empty_detection(self, line: str, expected: bool):
        assert _is_header_or_empty(line) is expected


class TestEvidenceSourceExtraction:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("disk full [Source: journald]", "journald"),
            ("prefix [Source: pacman.log] suffix", "pacman.log"),
            ("[source: lowercase]", "lowercase"),
            ("[SOURCE: uppercase]", "uppercase"),
            ("[Source:   padded source   ]", "padded source"),
            ("first [Source: one] second [Source: two]", "one"),
            ("[Source: ]", ""),
            ("[Source: path=/var/log/messages]", "path=/var/log/messages"),
            ("[Source: kernel-0]", "kernel-0"),
            ("no source tag", None),
            ("[Sources: plural]", None),
            ("[Source missing bracket", None),
        ],
    )
    def test_extract_source_handles_variants(self, line: str, expected: str | None):
        assert _extract_source(line) == expected


class TestStatusTrackerEdgeCases:
    def test_initial_status_is_healthy(self):
        tracker = StatusTracker()
        assert tracker.status == SystemStatus.HEALTHY

    def test_llm_unavailable_degrades_and_recovers(self):
        tracker = StatusTracker()
        tracker.set_llm_available(False)
        assert tracker.status == SystemStatus.DEGRADED
        tracker.set_llm_available(True)
        assert tracker.status == SystemStatus.HEALTHY

    def test_warning_event_degrades_status(self):
        tracker = StatusTracker()
        tracker.on_event(_event(Severity.WARNING))
        assert tracker.status == SystemStatus.DEGRADED

    def test_critical_event_overrides_llm_degraded_status(self):
        tracker = StatusTracker()
        tracker.set_llm_available(False)
        tracker.on_event(_event(Severity.CRITICAL, event_type=EventType.SERVICE_FAIL))
        assert tracker.status == SystemStatus.CRITICAL

    def test_duplicate_warning_transitions_fire_once(self):
        tracker = StatusTracker()
        transitions: list[tuple[SystemStatus, SystemStatus]] = []
        tracker.on_status_change(lambda old, new: transitions.append((old, new)))

        tracker.on_event(_event(Severity.WARNING))
        tracker.on_event(_event(Severity.WARNING))

        assert transitions == [(SystemStatus.HEALTHY, SystemStatus.DEGRADED)]

    def test_prune_removes_stale_warning_back_to_healthy(self):
        tracker = StatusTracker()
        tracker.on_event(_event(Severity.WARNING))
        tracker._recent_warnings[0].timestamp = datetime.now(timezone.utc) - STATUS_WINDOW - timedelta(seconds=1)

        tracker.prune()

        assert tracker.status == SystemStatus.HEALTHY

    def test_prune_removes_stale_critical_but_keeps_llm_degraded(self):
        tracker = StatusTracker()
        tracker.set_llm_available(False)
        tracker.on_event(_event(Severity.CRITICAL, event_type=EventType.SERVICE_FAIL))
        tracker._recent_criticals[0].timestamp = datetime.now(timezone.utc) - STATUS_WINDOW - timedelta(seconds=1)

        tracker.prune()

        assert tracker.status == SystemStatus.DEGRADED

    def test_old_event_is_ignored_after_pruning_window(self):
        tracker = StatusTracker()
        old_ts = datetime.now(timezone.utc) - STATUS_WINDOW - timedelta(minutes=1)
        tracker.on_event(_event(Severity.CRITICAL, timestamp=old_ts))
        assert tracker.status == SystemStatus.HEALTHY


class TestHookManagerEdgeCases:
    def test_action_allowed_when_plugins_return_true_or_none(self):
        manager = HookManager()
        manager.register(_TrueActionPlugin())
        manager.register(_NoneActionPlugin())
        assert manager.fire_action_requested("noop", {}) is True

    def test_action_denied_when_any_plugin_returns_false(self):
        manager = HookManager()
        manager.register(_NoneActionPlugin())
        manager.register(_FalseActionPlugin())
        assert manager.fire_action_requested("noop", {}) is False

    def test_fire_event_with_no_plugins_is_noop(self):
        manager = HookManager()
        manager.fire_event(_event(Severity.INFO))

    def test_unregister_unknown_plugin_is_safe(self):
        manager = HookManager()
        manager.unregister(object())

    def test_partial_plugin_receives_startup_and_shutdown(self):
        manager = HookManager()
        plugin = _StartupPlugin()
        manager.register(plugin)

        manager.fire_startup()
        manager.fire_shutdown()

        assert plugin.started is True
        assert plugin.stopped is True

    def test_duplicate_registration_raises_value_error(self):
        manager = HookManager()
        plugin = _StartupPlugin()
        manager.register(plugin)
        with pytest.raises(ValueError):
            manager.register(plugin)


class TestCronMatchingEdgeCases:
    @pytest.mark.parametrize(
        ("expr", "dt", "expected"),
        [
            ("0 6 * * *", datetime(2026, 3, 27, 6, 0), True),
            ("0 6 * * *", datetime(2026, 3, 27, 6, 1), False),
            ("15,30 6 * * *", datetime(2026, 3, 27, 6, 15), True),
            ("15,30 6 * * *", datetime(2026, 3, 27, 6, 20), False),
            ("*/15 6 * * *", datetime(2026, 3, 27, 6, 45), True),
            ("*/15 6 * * *", datetime(2026, 3, 27, 6, 46), False),
            ("10-12 6 * * *", datetime(2026, 3, 27, 6, 11), True),
            ("10-12 6 * * *", datetime(2026, 3, 27, 6, 13), False),
            ("10-20/5 6 * * *", datetime(2026, 3, 27, 6, 15), True),
            ("10-20/5 6 * * *", datetime(2026, 3, 27, 6, 16), False),
            ("0 */2 * * *", datetime(2026, 3, 27, 8, 0), True),
            ("0 */2 * * *", datetime(2026, 3, 27, 9, 0), False),
            ("0 6 1,15 * *", datetime(2026, 3, 15, 6, 0), True),
            ("0 6 1,15 * *", datetime(2026, 3, 16, 6, 0), False),
            ("0 6 * 3-5 *", datetime(2026, 4, 27, 6, 0), True),
            ("0 6 * 3-5 *", datetime(2026, 6, 27, 6, 0), False),
            ("0 6 * * 0-4", datetime(2026, 3, 30, 6, 0), True),
            ("0 6 * * 0-4", datetime(2026, 3, 29, 6, 0), False),
        ],
    )
    def test_cron_matching_supports_ranges_steps_and_lists(
        self,
        expr: str,
        dt: datetime,
        expected: bool,
    ):
        assert _cron_matches(_parse_cron(expr), dt) is expected

    @pytest.mark.parametrize(
        "fields",
        [
            {"minute": "nope", "hour": "*", "day": "*", "month": "*", "day_of_week": "*"},
            {"minute": "1-a", "hour": "*", "day": "*", "month": "*", "day_of_week": "*"},
            {"minute": "*/x", "hour": "*", "day": "*", "month": "*", "day_of_week": "*"},
            {"minute": "1-5/y", "hour": "*", "day": "*", "month": "*", "day_of_week": "*"},
        ],
    )
    def test_invalid_cron_tokens_raise_value_error(self, fields: dict[str, str]):
        with pytest.raises(ValueError):
            _cron_matches(fields, datetime(2026, 3, 27, 6, 0))


class TestLoggingEdgeCases:
    def test_setup_logging_creates_file_and_handlers(self, tmp_path: Path):
        with _isolated_root_logging() as root:
            log_dir = tmp_path / "logs"
            setup_logging(log_dir=log_dir)
            assert len(root.handlers) == 2
            assert (log_dir / "ailm.log").exists()

    def test_setup_logging_is_idempotent(self, tmp_path: Path):
        with _isolated_root_logging() as root:
            setup_logging(log_dir=tmp_path)
            first_handlers = list(root.handlers)
            setup_logging(log_dir=tmp_path)
            assert root.handlers == first_handlers

    def test_invalid_console_level_falls_back_to_info(self, tmp_path: Path):
        with _isolated_root_logging() as root:
            setup_logging(level="definitely-not-real", log_dir=tmp_path)
            assert root.handlers[0].level == logging.INFO

    def test_custom_log_dir_is_respected(self, tmp_path: Path):
        with _isolated_root_logging():
            log_dir = tmp_path / "custom"
            setup_logging(log_dir=log_dir)
            assert (log_dir / "ailm.log").exists()


class TestCliEntryPointEdgeCases:
    def test_dump_config_prints_and_exits(self, monkeypatch: pytest.MonkeyPatch, capsys):
        config = AilmConfig()
        monkeypatch.setattr(sys, "argv", ["ailm", "--dump-config"])
        monkeypatch.setattr("ailm.core.logging.setup_logging", Mock())
        monkeypatch.setattr("ailm.config.load_config", Mock(return_value=config))
        monkeypatch.setattr("ailm.config.dump_config", Mock(return_value="[db]\npath = \"x\""))

        with pytest.raises(SystemExit) as exc:
            cli.main()

        assert exc.value.code == 0
        assert "[db]" in capsys.readouterr().out

    def test_no_ui_path_uses_asyncio_run(self, monkeypatch: pytest.MonkeyPatch):
        config = AilmConfig()
        run_headless = AsyncMock()
        recorded: dict[str, bool] = {}

        def fake_run(coro) -> None:
            recorded["called"] = True
            coro.close()

        monkeypatch.setattr(sys, "argv", ["ailm", "--no-ui"])
        monkeypatch.setattr("ailm.core.logging.setup_logging", Mock())
        monkeypatch.setattr("ailm.config.load_config", Mock(return_value=config))
        monkeypatch.setattr(cli, "run_headless", run_headless)
        monkeypatch.setattr(cli.asyncio, "run", fake_run)

        cli.main()

        run_headless.assert_called_once_with(config)
        assert recorded["called"] is True

    def test_default_path_uses_ui_runner(self, monkeypatch: pytest.MonkeyPatch):
        config = AilmConfig()
        run_with_ui = Mock()

        monkeypatch.setattr(sys, "argv", ["ailm"])
        monkeypatch.setattr("ailm.core.logging.setup_logging", Mock())
        monkeypatch.setattr("ailm.config.load_config", Mock(return_value=config))
        monkeypatch.setattr(cli, "run_with_ui", run_with_ui)

        cli.main()

        run_with_ui.assert_called_once_with(config)

    def test_version_flag_exits_early(self, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ailm", "--version"])
        with pytest.raises(SystemExit) as exc:
            cli.main()

        assert exc.value.code == 0
        assert "0.2.0-dev" in capsys.readouterr().out


class TestSystemdInitEdgeCases:
    async def test_get_failed_units_returns_empty_when_systemctl_missing(self):
        async def raise_missing(*args, **kwargs):
            raise FileNotFoundError

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("ailm.distro.arch.asyncio.create_subprocess_exec", raise_missing)
            units = await SystemdInit().get_failed_units()

        assert units == []

    async def test_get_failed_units_parses_first_column_only(self):
        async def fake_exec(*args, **kwargs):
            return _FakeProc(
                stdout=(
                    b"nginx.service loaded failed failed Example\n"
                    b"sshd.service loaded failed failed Example\n"
                ),
            )

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("ailm.distro.arch.asyncio.create_subprocess_exec", fake_exec)
            units = await SystemdInit().get_failed_units()

        assert units == ["nginx.service", "sshd.service"]

    async def test_restart_unit_returns_true_on_success(self):
        async def fake_exec(*args, **kwargs):
            return _FakeProc(returncode=0)

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("ailm.distro.arch.asyncio.create_subprocess_exec", fake_exec)
            assert await SystemdInit().restart_unit("nginx.service") is True

    async def test_restart_unit_returns_false_and_logs_warning_on_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ):
        async def fake_exec(*args, **kwargs):
            return _FakeProc(stderr=b"permission denied", returncode=1)

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("ailm.distro.arch.asyncio.create_subprocess_exec", fake_exec)
            with caplog.at_level(logging.WARNING):
                result = await SystemdInit().restart_unit("nginx.service")

        assert result is False
        assert "permission denied" in caplog.text


class TestLlmQueueEdgeCases:
    async def test_task_within_max_age_is_processed(self):
        client = _QueueClient(["ok"])
        queue = LLMTaskQueue()
        queue.enqueue(
            LLMTask(
                prompt="fresh",
                created=datetime.now(timezone.utc) - MAX_AGE + timedelta(seconds=1),
            )
        )

        processed = await queue.drain(client)

        assert processed == 1
        assert queue.pending == 0

    async def test_callbacks_run_in_enqueue_order(self):
        client = _QueueClient(["first-result", "second-result"])
        queue = LLMTaskQueue()
        seen: list[str] = []

        async def record(result: str) -> None:
            seen.append(result)

        queue.enqueue(LLMTask(prompt="first", callback=record))
        queue.enqueue(LLMTask(prompt="second", callback=record))

        processed = await queue.drain(client)

        assert processed == 2
        assert seen == ["first-result", "second-result"]

    async def test_callback_exception_propagates_after_task_is_removed(self):
        client = _QueueClient(["result"])
        queue = LLMTaskQueue()

        async def boom(_result: str) -> None:
            raise RuntimeError("callback failed")

        queue.enqueue(LLMTask(prompt="task", callback=boom))

        with pytest.raises(RuntimeError, match="callback failed"):
            await queue.drain(client)

        assert queue.pending == 0

    async def test_generate_none_leaves_remaining_tasks_queued(self):
        client = _QueueClient(["first", None])
        queue = LLMTaskQueue()
        queue.enqueue(LLMTask(prompt="first"))
        queue.enqueue(LLMTask(prompt="second"))

        processed = await queue.drain(client)

        assert processed == 1
        assert queue.pending == 1
        assert queue._tasks[0].prompt == "second"

    def test_clear_empty_queue_is_safe(self):
        queue = LLMTaskQueue()
        queue.clear()
        assert queue.pending == 0


class TestRepresentationEdgeCases:
    def test_system_event_repr_highlights_debug_fields(self):
        event = _event(Severity.WARNING)
        text = repr(event)
        assert "SystemEvent" in text
        assert "disk_alert" in text
        assert "warning" in text

    def test_ailm_config_repr_lists_major_sections(self):
        text = repr(AilmConfig())
        assert "AilmConfig" in text
        assert "llm=" in text
        assert "db=" in text

    def test_package_event_repr_includes_versions(self):
        package = PackageEvent(
            name="linux",
            action="upgraded",
            timestamp=datetime(2026, 3, 27, tzinfo=timezone.utc),
            old_version="6.1",
            new_version="6.2",
        )
        text = repr(package)
        assert "linux" in text
        assert "6.1" in text
        assert "6.2" in text

    def test_llm_task_repr_mentions_callback_presence(self):
        async def callback(_result: str) -> None:
            return None

        text = repr(LLMTask(prompt="hello", callback=callback))
        assert "LLMTask" in text
        assert "has_callback=True" in text

    def test_journal_entry_repr_includes_unit_and_priority(self):
        entry = JournalEntry(
            message="OOM killed process",
            unit="kernel",
            priority=3,
            timestamp=datetime(2026, 3, 27, tzinfo=timezone.utc),
        )
        text = repr(entry)
        assert "kernel" in text
        assert "priority=3" in text


class TestSourceBaseEdgeCases:
    async def test_cancel_task_accepts_none(self):
        await cancel_task(None)

    async def test_cancel_task_cancels_running_task(self):
        task = asyncio.create_task(asyncio.sleep(10))
        await cancel_task(task)
        assert task.cancelled() is True

    async def test_polling_source_start_and_stop_manage_background_task(self):
        source = _CounterPollingSource()
        bus = EventBus()
        await bus.start()

        await source.start(bus)
        await _wait_for(lambda: source.calls > 0)
        calls_before_stop = source.calls
        await source.stop()
        await asyncio.sleep(0.03)
        await bus.stop()

        assert source.calls == calls_before_stop
        assert source._task is None

    async def test_watchdog_schedule_async_runs_coroutine(self):
        source = _DummyWatchdogSource()
        bus = EventBus()
        await bus.start()
        await source.start(bus)

        source._schedule_async(lambda: source.record("async"))
        await _wait_for(lambda: source.calls == ["async"])

        await source.stop()
        await bus.stop()

        assert source.observer.started is True
        assert source.observer.stopped is True
        assert source.observer.join_timeout == 5

    async def test_watchdog_schedule_debounced_collapses_rapid_events(self):
        source = _DummyWatchdogSource()
        bus = EventBus()
        await bus.start()
        await source.start(bus)

        source._schedule_debounced(lambda: source.record("first"))
        source._schedule_debounced(lambda: source.record("second"))
        source._schedule_debounced(lambda: source.record("third"))
        await asyncio.sleep(DEBOUNCE_SECONDS + 0.1)

        await source.stop()
        await bus.stop()

        assert source.calls == ["third"]

    async def test_watchdog_schedule_methods_before_start_are_noops(self):
        source = _DummyWatchdogSource()
        source._schedule_async(lambda: source.record("async"))
        source._schedule_debounced(lambda: source.record("debounced"))
        await asyncio.sleep(0.05)
        assert source.calls == []
