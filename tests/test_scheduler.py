"""Scheduler engine and morning briefing tests."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.db.connection import Database
from ailm.db.repository import EventRepository
from ailm.scheduler.briefing import (
    MAX_SUMMARY_CHARS,
    _build_events_summary,
    _build_fallback_briefing,
    generate_morning_briefing,
)
from ailm.scheduler.engine import SchedulerEngine, _parse_cron


# --- Module-level job functions (APScheduler v4 requires non-nested callables) ---

_cron_job_call_count = 0


async def _dummy_cron_job() -> None:
    global _cron_job_call_count
    _cron_job_call_count += 1


async def _dummy_interval_job() -> None:
    pass


# --- Helpers ---


def _make_event(
    event_type: EventType = EventType.DISK_ALERT,
    severity: Severity = Severity.WARNING,
    source: str = "test",
    summary: str | None = "test event",
    raw_data: str = "raw test data",
) -> SystemEvent:
    return SystemEvent(
        type=event_type,
        severity=severity,
        raw_data=raw_data,
        source=source,
        summary=summary,
    )


def _make_mock_db() -> MagicMock:
    """Create a mock Database with a mock connection."""
    db = MagicMock(spec=Database)
    db.conn = MagicMock()
    db.conn.execute = AsyncMock()
    db.conn.commit = AsyncMock()
    db.conn.execute_fetchall = AsyncMock(return_value=[])
    return db


def _make_mock_llm(available: bool = True, briefing_text: str | None = None) -> MagicMock:
    """Create a mock OllamaClient."""
    from ailm.llm.client import OllamaClient

    llm = MagicMock(spec=OllamaClient)
    llm.available = available
    if briefing_text is not None:
        llm.generate_briefing = AsyncMock(return_value=briefing_text)
    else:
        llm.generate_briefing = AsyncMock(return_value=None)
    return llm


# --- Cron parsing ---


class TestCronParsing:
    def test_parse_standard_cron(self):
        trigger = _parse_cron("0 6 * * *")
        assert trigger is not None

    def test_parse_complex_cron(self):
        trigger = _parse_cron("30 */2 * * 1-5")
        assert trigger is not None

    def test_invalid_field_count_raises(self):
        with pytest.raises(ValueError, match="5-field"):
            _parse_cron("0 6 * *")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="5-field"):
            _parse_cron("")

    def test_six_fields_raises(self):
        with pytest.raises(ValueError, match="5-field"):
            _parse_cron("0 0 6 * * *")


# --- Events summary ---


class TestBuildEventsSummary:
    def test_empty_events(self):
        summary = _build_events_summary([])
        assert "No events" in summary

    def test_single_event_included(self):
        events = [_make_event(summary="disk at 82%")]
        summary = _build_events_summary(events)
        assert "disk at 82%" in summary
        assert "Total events: 1" in summary

    def test_multiple_types_counted(self):
        events = [
            _make_event(EventType.DISK_ALERT),
            _make_event(EventType.DISK_ALERT),
            _make_event(EventType.SERVICE_FAIL, Severity.CRITICAL),
        ]
        summary = _build_events_summary(events)
        assert "disk_alert: 2" in summary
        assert "service_fail: 1" in summary
        assert "Total events: 3" in summary

    def test_critical_events_listed_first(self):
        events = [
            _make_event(EventType.DISK_ALERT, Severity.INFO, summary="info event"),
            _make_event(EventType.SERVICE_FAIL, Severity.CRITICAL, summary="critical event"),
        ]
        summary = _build_events_summary(events)
        crit_pos = summary.index("critical event")
        info_pos = summary.index("info event")
        assert crit_pos < info_pos

    def test_summary_within_budget(self):
        """Summary must not exceed the character budget."""
        events = [
            _make_event(summary=f"event number {i} with a somewhat long description")
            for i in range(100)
        ]
        summary = _build_events_summary(events)
        assert len(summary) <= MAX_SUMMARY_CHARS

    def test_uses_raw_data_when_no_summary(self):
        events = [_make_event(summary=None, raw_data="raw data fallback")]
        summary = _build_events_summary(events)
        assert "raw data fallback" in summary


# --- Fallback briefing ---


class TestFallbackBriefing:
    def test_empty_events(self):
        text = _build_fallback_briefing([])
        assert "No events" in text
        assert "quiet" in text.lower()

    def test_includes_severity_counts(self):
        events = [
            _make_event(severity=Severity.CRITICAL),
            _make_event(severity=Severity.WARNING),
            _make_event(severity=Severity.WARNING),
            _make_event(severity=Severity.INFO),
        ]
        text = _build_fallback_briefing(events)
        assert "critical: 1" in text
        assert "warning: 2" in text
        assert "info: 1" in text

    def test_includes_type_counts(self):
        events = [
            _make_event(EventType.DISK_ALERT),
            _make_event(EventType.PACKAGE_UPDATE),
            _make_event(EventType.DISK_ALERT),
        ]
        text = _build_fallback_briefing(events)
        assert "disk_alert: 2" in text
        assert "package_update: 1" in text

    def test_highlights_critical_events(self):
        events = [
            _make_event(EventType.SERVICE_FAIL, Severity.CRITICAL, summary="nginx down"),
        ]
        text = _build_fallback_briefing(events)
        assert "Critical events" in text
        assert "nginx down" in text

    def test_no_critical_section_when_none(self):
        events = [_make_event(severity=Severity.INFO)]
        text = _build_fallback_briefing(events)
        assert "Critical events" not in text

    def test_mentions_llm_unavailable(self):
        events = [_make_event()]
        text = _build_fallback_briefing(events)
        assert "LLM unavailable" in text


# --- generate_morning_briefing ---


class TestGenerateMorningBriefing:
    async def test_briefing_with_llm(self):
        """LLM available: uses LLM-generated text."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=True, briefing_text="All systems healthy.")
        bus = EventBus()

        received: list[SystemEvent] = []
        bus.subscribe(EventType.BRIEFING, received.append)
        await bus.start()

        # Mock the repo to return some events
        mock_events = [
            _make_event(EventType.DISK_ALERT, summary="disk at 45%"),
            _make_event(EventType.PACKAGE_UPDATE, Severity.INFO, summary="3 packages updated"),
        ]
        with patch.object(EventRepository, "get_events_since", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_events
            with patch.object(EventRepository, "insert_event", new_callable=AsyncMock) as mock_insert:
                mock_insert.return_value = 1
                await generate_morning_briefing(db, llm, bus)

        await bus.stop()

        # LLM was called
        llm.generate_briefing.assert_awaited_once()

        # Event was published
        assert len(received) == 1
        assert received[0].type == EventType.BRIEFING
        assert received[0].severity == Severity.INFO
        assert received[0].summary == "All systems healthy."
        assert received[0].source == "scheduler"

    async def test_fallback_when_llm_unavailable(self):
        """LLM unavailable: falls back to plain text."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=False)
        bus = EventBus()

        received: list[SystemEvent] = []
        bus.subscribe(EventType.BRIEFING, received.append)
        await bus.start()

        mock_events = [
            _make_event(EventType.SERVICE_FAIL, Severity.CRITICAL, summary="sshd crashed"),
        ]
        with patch.object(EventRepository, "get_events_since", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_events
            with patch.object(EventRepository, "insert_event", new_callable=AsyncMock) as mock_insert:
                mock_insert.return_value = 1
                await generate_morning_briefing(db, llm, bus)

        await bus.stop()

        # LLM was NOT called
        llm.generate_briefing.assert_not_awaited()

        # Fallback briefing published
        assert len(received) == 1
        assert "LLM unavailable" in received[0].summary
        assert "sshd crashed" in received[0].summary

    async def test_fallback_when_llm_returns_none(self):
        """LLM available but returns None: falls back to plain text."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=True, briefing_text=None)
        bus = EventBus()

        received: list[SystemEvent] = []
        bus.subscribe(EventType.BRIEFING, received.append)
        await bus.start()

        with patch.object(EventRepository, "get_events_since", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [_make_event()]
            with patch.object(EventRepository, "insert_event", new_callable=AsyncMock) as mock_insert:
                mock_insert.return_value = 1
                await generate_morning_briefing(db, llm, bus)

        await bus.stop()

        assert len(received) == 1
        assert "LLM unavailable" in received[0].summary

    async def test_empty_events(self):
        """No events in last 24h: still generates a briefing."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=True, briefing_text="Nothing happened. All quiet.")
        bus = EventBus()

        received: list[SystemEvent] = []
        bus.subscribe(EventType.BRIEFING, received.append)
        await bus.start()

        with patch.object(EventRepository, "get_events_since", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            with patch.object(EventRepository, "insert_event", new_callable=AsyncMock) as mock_insert:
                mock_insert.return_value = 1
                await generate_morning_briefing(db, llm, bus)

        await bus.stop()

        assert len(received) == 1
        assert received[0].summary == "Nothing happened. All quiet."

    async def test_empty_events_fallback(self):
        """No events + no LLM: still generates a fallback briefing."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=False)
        bus = EventBus()

        received: list[SystemEvent] = []
        bus.subscribe(EventType.BRIEFING, received.append)
        await bus.start()

        with patch.object(EventRepository, "get_events_since", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            with patch.object(EventRepository, "insert_event", new_callable=AsyncMock) as mock_insert:
                mock_insert.return_value = 1
                await generate_morning_briefing(db, llm, bus)

        await bus.stop()

        assert len(received) == 1
        assert "No events" in received[0].summary

    async def test_context_budget_not_exceeded(self):
        """Even with many events, the summary stays within budget."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=True)
        bus = EventBus()
        await bus.start()

        many_events = [
            _make_event(summary=f"event {i} " * 10)
            for i in range(200)
        ]

        captured_summary: list[str] = []
        original_generate = llm.generate_briefing

        async def capture_summary(s: str) -> str | None:
            captured_summary.append(s)
            return "briefing"

        llm.generate_briefing = capture_summary

        with patch.object(EventRepository, "get_events_since", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = many_events
            with patch.object(EventRepository, "insert_event", new_callable=AsyncMock) as mock_insert:
                mock_insert.return_value = 1
                await generate_morning_briefing(db, llm, bus)

        await bus.stop()

        assert len(captured_summary) == 1
        assert len(captured_summary[0]) <= MAX_SUMMARY_CHARS

    async def test_briefing_event_stored_in_db(self):
        """The briefing event is persisted via insert_event."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=True, briefing_text="All good.")
        bus = EventBus()
        await bus.start()

        with patch.object(EventRepository, "get_events_since", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            with patch.object(EventRepository, "insert_event", new_callable=AsyncMock) as mock_insert:
                mock_insert.return_value = 1
                await generate_morning_briefing(db, llm, bus)

        await bus.stop()

        mock_insert.assert_awaited_once()
        stored_event = mock_insert.call_args[0][0]
        assert stored_event.type == EventType.BRIEFING
        assert stored_event.summary == "All good."

    async def test_db_query_failure_handled_gracefully(self):
        """If DB query fails, briefing doesn't crash."""
        db = _make_mock_db()
        llm = _make_mock_llm(available=True)
        bus = EventBus()
        await bus.start()

        with patch.object(
            EventRepository, "get_events_since", new_callable=AsyncMock, side_effect=RuntimeError("DB error")
        ):
            # Should not raise
            await generate_morning_briefing(db, llm, bus)

        await bus.stop()


# --- SchedulerEngine lifecycle ---


class TestSchedulerEngine:
    async def test_start_stop_lifecycle(self):
        engine = SchedulerEngine()
        assert not engine.running

        await engine.start()
        assert engine.running

        await engine.stop()
        assert not engine.running

    async def test_double_start_is_noop(self):
        engine = SchedulerEngine()
        await engine.start()
        await engine.start()  # should not raise
        assert engine.running
        await engine.stop()

    async def test_stop_without_start_is_noop(self):
        engine = SchedulerEngine()
        await engine.stop()  # should not raise
        assert not engine.running

    async def test_add_cron_job(self):
        engine = SchedulerEngine()
        await engine.start()

        await engine.add_cron_job(_dummy_cron_job, "0 6 * * *", "morning-briefing")
        assert len(engine._jobs) == 1

        await engine.stop()

    async def test_add_interval_job(self):
        engine = SchedulerEngine()
        await engine.start()

        await engine.add_interval_job(_dummy_interval_job, 300, "health-check")
        assert len(engine._jobs) == 1

        await engine.stop()

    async def test_restart_after_stop(self):
        """Engine can be restarted after stopping."""
        engine = SchedulerEngine()
        await engine.start()
        assert engine.running
        await engine.stop()
        assert not engine.running

        # Need fresh scheduler instance for restart
        engine = SchedulerEngine()
        await engine.start()
        assert engine.running
        await engine.stop()
