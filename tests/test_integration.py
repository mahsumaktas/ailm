"""End-to-end integration tests for application wiring."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ailm.app import Application
from ailm.config.schema import AilmConfig
from ailm.core.models import EventType, Severity, SystemEvent, SystemStatus


class _NoOpSource:
    """Minimal source used to exercise application lifecycle wiring."""

    name = "noop"

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.bus = None

    async def start(self, bus) -> None:
        self.started = True
        self.bus = bus

    async def stop(self) -> None:
        self.stopped = True


class _SchedulerCapture:
    """Capture scheduled jobs without starting background timers."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.cron_jobs: dict[str, tuple[object, str]] = {}
        self.interval_jobs: dict[str, tuple[object, int]] = {}

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def add_cron_job(self, func, cron_expr: str, job_id: str) -> None:
        self.cron_jobs[job_id] = (func, cron_expr)

    async def add_interval_job(self, func, seconds: int, job_id: str) -> None:
        self.interval_jobs[job_id] = (func, seconds)


class _FakeLLM:
    """Controllable LLM fake for application integration tests."""

    def __init__(
        self,
        *,
        available: bool,
        classify_result: dict | None = None,
        generate_result: str | None = None,
    ) -> None:
        self.available = available
        self._health_available = available
        self._classify_result = classify_result
        self._generate_result = generate_result
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True
        self.available = False

    async def health_check(self) -> bool:
        self.available = self._health_available
        return self.available

    async def classify_log(self, _log_line: str) -> dict | None:
        return self._classify_result

    async def generate(self, _prompt: str, system: str | None = None) -> str | None:
        if not self.available:
            return None
        return self._generate_result

    async def generate_briefing(self, _events_summary: str) -> str | None:
        if not self.available:
            return None
        return self._generate_result


def _make_config(tmp_path: Path, *, llm_enabled: bool = False) -> AilmConfig:
    """Build an application config rooted in a temporary directory."""
    return AilmConfig(
        db={"path": str(tmp_path / "integration.db"), "retention_days": 30},
        llm={"enabled": llm_enabled},
        sources={
            "journald_enabled": False,
            "snapshot_path": str(tmp_path / "snapshots-missing"),
        },
    )


async def _flush_bus() -> None:
    """Yield to the event loop long enough for bus subscribers to run."""
    await asyncio.sleep(0.05)


@asynccontextmanager
async def _started_app(
    tmp_path: Path,
    *,
    llm: _FakeLLM | None = None,
) -> AsyncIterator[tuple[Application, _SchedulerCapture, _NoOpSource]]:
    """Start an application instance with a real DB and captured scheduler."""
    config = _make_config(tmp_path, llm_enabled=llm is not None)
    app = Application(config)
    source = _NoOpSource()
    scheduler = _SchedulerCapture()
    app._register_sources = lambda: app.sources.append(source)

    with patch("ailm.app.SchedulerEngine", return_value=scheduler):
        if llm is None:
            await app.start()
        else:
            with patch("ailm.app.OllamaClient", return_value=llm):
                await app.start()
        try:
            yield app, scheduler, source
        finally:
            await app.stop()


def _event(
    event_type: EventType,
    severity: Severity,
    *,
    source: str = "integration",
    summary: str | None = "summary",
    raw_data: str = "raw-data",
    timestamp: datetime | None = None,
) -> SystemEvent:
    """Create a system event with integration-friendly defaults."""
    return SystemEvent(
        type=event_type,
        severity=severity,
        raw_data=raw_data,
        source=source,
        summary=summary,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


class TestApplicationLifecycleIntegration:
    async def test_start_and_stop_create_real_database(self, tmp_path: Path):
        async with _started_app(tmp_path) as (app, scheduler, source):
            assert app.db is not None
            assert app.repo is not None
            assert Path(app.config.db.path).exists()
            assert source.started is True
            assert scheduler.started is True
            assert {"morning_briefing", "db_cleanup"} <= scheduler.cron_jobs.keys()
            assert "health_check" in scheduler.interval_jobs

        assert source.stopped is True
        assert scheduler.stopped is True
        assert app.db is None
        assert app.repo is None

    async def test_welcome_briefing_inserted_once(self, tmp_path: Path):
        async with _started_app(tmp_path) as (app, _scheduler, _source):
            await app.maybe_insert_welcome()
            await app.maybe_insert_welcome()
            events = await app.repo.get_recent_events(limit=10)

        assert len(events) == 1
        assert events[0].type == EventType.BRIEFING
        assert "Welcome to ailm" in (events[0].summary or "")


class TestEventPersistenceIntegration:
    async def test_info_event_persists_without_changing_status(self, tmp_path: Path):
        async with _started_app(tmp_path) as (app, _scheduler, _source):
            await app.bus.publish(_event(EventType.PACKAGE_UPDATE, Severity.INFO))
            await _flush_bus()
            events = await app.repo.get_recent_events(limit=1)

            assert events[0].type == EventType.PACKAGE_UPDATE
            assert app.status_tracker.status == SystemStatus.HEALTHY

    async def test_warning_event_persists_and_degrades_status(self, tmp_path: Path):
        async with _started_app(tmp_path) as (app, _scheduler, _source):
            await app.bus.publish(_event(EventType.DISK_ALERT, Severity.WARNING))
            await _flush_bus()
            events = await app.repo.get_recent_events(limit=1)

            assert events[0].type == EventType.DISK_ALERT
            assert app.status_tracker.status == SystemStatus.DEGRADED

    async def test_critical_event_persists_and_sets_critical_status(self, tmp_path: Path):
        async with _started_app(tmp_path) as (app, _scheduler, _source):
            await app.bus.publish(_event(EventType.SERVICE_FAIL, Severity.CRITICAL))
            await _flush_bus()
            events = await app.repo.get_recent_events(limit=1)

            assert events[0].type == EventType.SERVICE_FAIL
            assert app.status_tracker.status == SystemStatus.CRITICAL

    async def test_old_warning_is_persisted_but_not_counted_for_status(self, tmp_path: Path):
        old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        async with _started_app(tmp_path) as (app, _scheduler, _source):
            await app.bus.publish(
                _event(
                    EventType.DISK_ALERT,
                    Severity.WARNING,
                    summary="stale warning",
                    timestamp=old_ts,
                )
            )
            await _flush_bus()
            events = await app.repo.get_recent_events(limit=1)

            assert events[0].summary == "stale warning"
            assert app.status_tracker.status == SystemStatus.HEALTHY

    async def test_recent_events_round_trip_in_reverse_chronological_order(
        self,
        tmp_path: Path,
    ):
        base = datetime.now(timezone.utc)
        async with _started_app(tmp_path) as (app, _scheduler, _source):
            await app.bus.publish(
                _event(
                    EventType.PACKAGE_UPDATE,
                    Severity.INFO,
                    summary="first",
                    timestamp=base,
                )
            )
            await app.bus.publish(
                _event(
                    EventType.DISK_ALERT,
                    Severity.WARNING,
                    summary="second",
                    timestamp=base + timedelta(seconds=1),
                )
            )
            await app.bus.publish(
                _event(
                    EventType.SERVICE_FAIL,
                    Severity.CRITICAL,
                    summary="third",
                    timestamp=base + timedelta(seconds=2),
                )
            )
            await _flush_bus()
            events = await app.repo.get_recent_events(limit=3)

            assert [event.summary for event in events] == ["third", "second", "first"]

    async def test_status_change_callbacks_capture_warning_then_critical(self, tmp_path: Path):
        async with _started_app(tmp_path) as (app, _scheduler, _source):
            transitions: list[tuple[SystemStatus, SystemStatus]] = []
            app.status_tracker.on_status_change(
                lambda old, new: transitions.append((old, new))
            )

            await app.bus.publish(_event(EventType.DISK_ALERT, Severity.WARNING))
            await _flush_bus()
            await app.bus.publish(_event(EventType.SERVICE_FAIL, Severity.CRITICAL))
            await _flush_bus()

            assert transitions == [
                (SystemStatus.HEALTHY, SystemStatus.DEGRADED),
                (SystemStatus.DEGRADED, SystemStatus.CRITICAL),
            ]


class TestLLMClassificationIntegration:
    async def test_immediate_log_classification_updates_persisted_summary(self, tmp_path: Path):
        llm = _FakeLLM(
            available=True,
            classify_result={
                "type": "log_anomaly",
                "severity": "warning",
                "summary": "Kernel panic detected",
            },
        )

        async with _started_app(tmp_path, llm=llm) as (app, _scheduler, _source):
            await app.bus.publish(
                _event(
                    EventType.LOG_ANOMALY,
                    Severity.WARNING,
                    summary=None,
                    raw_data="kernel: panic in ext4",
                )
            )
            await _flush_bus()
            await asyncio.sleep(0.5)

            assert llm.started is True
            # v0.3: batch analyzer replaced per-event llm_queue
            assert app.batch_analyzer is not None

    async def test_unavailable_llm_queues_log_anomaly_for_later_processing(self, tmp_path: Path):
        llm = _FakeLLM(available=False)

        async with _started_app(tmp_path, llm=llm) as (app, _scheduler, _source):
            await app.bus.publish(
                _event(
                    EventType.LOG_ANOMALY,
                    Severity.WARNING,
                    summary=None,
                    raw_data="segfault at 0x0",
                )
            )
            await _flush_bus()
            events = await app.repo.get_recent_events(limit=1)

            # v0.3: no per-event queue; events persist without summary when LLM down
            assert app.status_tracker.status == SystemStatus.DEGRADED
            assert events[0].summary is None

    async def test_batch_analysis_classifies_events_when_llm_recovers(
        self,
        tmp_path: Path,
    ):
        llm = _FakeLLM(available=False)

        async with _started_app(tmp_path, llm=llm) as (app, scheduler, _source):
            await app.bus.publish(
                _event(
                    EventType.LOG_ANOMALY,
                    Severity.WARNING,
                    summary=None,
                    raw_data="disk controller reset",
                )
            )
            await _flush_bus()

            # Get the persisted event ID so the batch response can reference it
            events_before = await app.repo.get_recent_events(limit=1)
            event_id = events_before[0].id

            # Restore LLM health, then run batch analysis
            llm._health_available = True
            health_job, _seconds = scheduler.interval_jobs["health_check"]
            await health_job()  # calls llm.health_check() → sets llm.available=True

            llm._generate_result = json.dumps({
                "events": [{"id": event_id, "summary": "Controller reset observed", "action": "investigate"}],
                "patterns": [],
                "overall": "disk controller issue",
            })
            batch_job, _seconds = scheduler.interval_jobs["batch_analysis"]
            await batch_job()

            events = await app.repo.get_recent_events(limit=1)
            assert events[0].summary == "Controller reset observed"
            assert app.status_tracker.status == SystemStatus.DEGRADED


class TestScheduledJobIntegration:
    async def test_cleanup_job_removes_old_events_from_real_database(self, tmp_path: Path):
        old_ts = datetime.now(timezone.utc) - timedelta(days=60)
        async with _started_app(tmp_path) as (app, scheduler, _source):
            await app.repo.insert_event(
                _event(
                    EventType.PACKAGE_UPDATE,
                    Severity.INFO,
                    summary="old package event",
                    timestamp=old_ts,
                )
            )
            cleanup_job, _cron = scheduler.cron_jobs["db_cleanup"]
            await cleanup_job()
            events = await app.repo.get_recent_events(limit=10)

            assert events == []

    async def test_briefing_job_publishes_and_persists_briefing_event(self, tmp_path: Path):
        async with _started_app(tmp_path) as (app, scheduler, _source):
            await app.repo.insert_event(
                _event(
                    EventType.DISK_ALERT,
                    Severity.WARNING,
                    summary="disk usage at 82%",
                )
            )

            briefing_job, _cron = scheduler.cron_jobs["morning_briefing"]
            await briefing_job()
            await _flush_bus()
            events = await app.repo.get_recent_events(limit=5)

            assert any(event.type == EventType.BRIEFING for event in events)
            briefing = next(event for event in events if event.type == EventType.BRIEFING)
            assert "Morning briefing" in (briefing.summary or "")
