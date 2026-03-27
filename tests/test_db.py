"""Database connection, schema, and repository tests."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.db.connection import Database
from ailm.db.repository import EventRepository


@pytest.fixture
async def db(tmp_path: Path):
    """Provide a fresh database for each test."""
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def repo(db: Database) -> EventRepository:
    return EventRepository(db)


def _make_event(
    event_type: EventType = EventType.DISK_ALERT,
    severity: Severity = Severity.WARNING,
    source: str = "test",
    summary: str | None = None,
    raw_data: str = "test data",
    timestamp: datetime | None = None,
    user_action: str | None = None,
) -> SystemEvent:
    return SystemEvent(
        type=event_type,
        severity=severity,
        raw_data=raw_data,
        source=source,
        summary=summary,
        timestamp=timestamp or datetime.now(timezone.utc),
        user_action=user_action,
    )


# --- Connection & Schema ---


class TestConnection:
    async def test_connect_creates_file(self, tmp_path: Path):
        db = Database(str(tmp_path / "new.db"))
        await db.connect()
        assert (tmp_path / "new.db").exists()
        await db.close()

    async def test_connect_creates_parent_dirs(self, tmp_path: Path):
        db = Database(str(tmp_path / "nested" / "dir" / "ailm.db"))
        await db.connect()
        assert (tmp_path / "nested" / "dir" / "ailm.db").exists()
        await db.close()

    async def test_wal_mode_enabled(self, db: Database):
        row = await db.conn.execute_fetchall("PRAGMA journal_mode")
        assert row[0][0] == "wal"

    async def test_tables_created(self, db: Database):
        tables = await db.conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {row[0] for row in tables}
        assert {"events", "preferences", "skills", "schema_version"} <= names

    async def test_schema_version_set(self, db: Database):
        rows = await db.conn.execute_fetchall("SELECT version FROM schema_version")
        assert len(rows) == 1
        assert rows[0][0] == 1

    async def test_idempotent_connect(self, tmp_path: Path):
        """Connecting twice to same DB doesn't duplicate schema_version."""
        path = str(tmp_path / "idempotent.db")
        db1 = Database(path)
        await db1.connect()
        await db1.close()

        db2 = Database(path)
        await db2.connect()
        rows = await db2.conn.execute_fetchall("SELECT version FROM schema_version")
        assert len(rows) == 1
        await db2.close()

    async def test_conn_property_before_connect_raises(self, tmp_path: Path):
        db = Database(str(tmp_path / "not_connected.db"))
        with pytest.raises(RuntimeError, match="not connected"):
            _ = db.conn


# --- Repository: Insert & Query ---


class TestRepositoryInsert:
    async def test_insert_returns_id(self, repo: EventRepository):
        event = _make_event()
        event_id = await repo.insert_event(event)
        assert event_id >= 1
        assert event.id == event_id

    async def test_insert_multiple(self, repo: EventRepository):
        id1 = await repo.insert_event(_make_event(source="a"))
        id2 = await repo.insert_event(_make_event(source="b"))
        assert id2 > id1

    async def test_insert_preserves_all_fields(self, repo: EventRepository):
        ts = datetime(2026, 3, 26, 10, 0, 0, tzinfo=timezone.utc)
        original = _make_event(
            event_type=EventType.BRIEFING,
            severity=Severity.INFO,
            source="scheduler",
            summary="Morning briefing",
            raw_data='{"events": 5}',
            timestamp=ts,
            user_action="read",
        )
        await repo.insert_event(original)

        events = await repo.get_recent_events(limit=1)
        loaded = events[0]
        assert loaded.type == EventType.BRIEFING
        assert loaded.severity == Severity.INFO
        assert loaded.source == "scheduler"
        assert loaded.summary == "Morning briefing"
        assert loaded.raw_data == '{"events": 5}'
        assert loaded.timestamp == ts
        assert loaded.user_action == "read"


class TestRepositoryQuery:
    async def test_get_recent_events(self, repo: EventRepository):
        for i in range(5):
            await repo.insert_event(_make_event(source=f"src-{i}"))

        events = await repo.get_recent_events(limit=3)
        assert len(events) == 3
        # Most recent first
        assert events[0].source == "src-4"

    async def test_get_events_since(self, repo: EventRepository):
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recent = datetime(2026, 3, 25, tzinfo=timezone.utc)
        now = datetime(2026, 3, 26, tzinfo=timezone.utc)

        await repo.insert_event(_make_event(timestamp=old, source="old"))
        await repo.insert_event(_make_event(timestamp=recent, source="recent"))
        await repo.insert_event(_make_event(timestamp=now, source="now"))

        events = await repo.get_events_since(datetime(2026, 3, 1, tzinfo=timezone.utc))
        assert len(events) == 2
        assert events[0].source == "recent"
        assert events[1].source == "now"

    async def test_get_events_since_with_type_filter(self, repo: EventRepository):
        now = datetime.now(timezone.utc)
        await repo.insert_event(_make_event(event_type=EventType.DISK_ALERT, timestamp=now))
        await repo.insert_event(_make_event(event_type=EventType.SERVICE_FAIL, timestamp=now))
        await repo.insert_event(_make_event(event_type=EventType.DISK_ALERT, timestamp=now))

        since = now - timedelta(minutes=1)
        disk_events = await repo.get_events_since(since, EventType.DISK_ALERT)
        assert len(disk_events) == 2
        assert all(e.type == EventType.DISK_ALERT for e in disk_events)

    async def test_get_recent_empty_db(self, repo: EventRepository):
        events = await repo.get_recent_events()
        assert events == []

    async def test_get_events_since_empty_db(self, repo: EventRepository):
        events = await repo.get_events_since(datetime.now(timezone.utc))
        assert events == []


class TestRepositoryUpdate:
    async def test_update_user_action(self, repo: EventRepository):
        event = _make_event()
        await repo.insert_event(event)
        assert event.user_action is None

        await repo.update_user_action(event.id, "ignored")

        events = await repo.get_recent_events(limit=1)
        assert events[0].user_action == "ignored"

    async def test_update_user_action_overwrite(self, repo: EventRepository):
        event = _make_event(user_action="postponed")
        await repo.insert_event(event)

        await repo.update_user_action(event.id, "applied")
        events = await repo.get_recent_events(limit=1)
        assert events[0].user_action == "applied"


class TestRepositoryAggregation:
    async def test_event_count_by_type(self, repo: EventRepository):
        now = datetime.now(timezone.utc)
        await repo.insert_event(_make_event(EventType.DISK_ALERT, timestamp=now))
        await repo.insert_event(_make_event(EventType.DISK_ALERT, timestamp=now))
        await repo.insert_event(_make_event(EventType.SERVICE_FAIL, timestamp=now))

        counts = await repo.get_event_count_by_type(now - timedelta(minutes=1))
        assert counts["disk_alert"] == 2
        assert counts["service_fail"] == 1

    async def test_event_count_empty(self, repo: EventRepository):
        counts = await repo.get_event_count_by_type(datetime.now(timezone.utc))
        assert counts == {}


# --- Retention Cleanup ---


class TestCleanup:
    async def test_cleanup_removes_old_events(self, repo: EventRepository):
        old = datetime.now(timezone.utc) - timedelta(days=60)
        recent = datetime.now(timezone.utc)

        await repo.insert_event(_make_event(timestamp=old, source="old"))
        await repo.insert_event(_make_event(timestamp=recent, source="recent"))

        deleted = await repo.cleanup_old_events(retention_days=30)
        assert deleted == 1

        remaining = await repo.get_recent_events()
        assert len(remaining) == 1
        assert remaining[0].source == "recent"

    async def test_cleanup_preserves_service_fail(self, repo: EventRepository):
        old = datetime.now(timezone.utc) - timedelta(days=60)
        await repo.insert_event(
            _make_event(event_type=EventType.SERVICE_FAIL, timestamp=old)
        )

        deleted = await repo.cleanup_old_events(retention_days=30)
        assert deleted == 0

    async def test_cleanup_preserves_applied_actions(self, repo: EventRepository):
        old = datetime.now(timezone.utc) - timedelta(days=60)
        await repo.insert_event(_make_event(timestamp=old, user_action="applied"))

        deleted = await repo.cleanup_old_events(retention_days=30)
        assert deleted == 0

    async def test_cleanup_on_empty_db(self, repo: EventRepository):
        deleted = await repo.cleanup_old_events(30)
        assert deleted == 0

    async def test_cleanup_critical_retention_cap(self, repo: EventRepository):
        """SERVICE_FAIL events are cleaned up after critical_retention_days."""
        very_old = datetime.now(timezone.utc) - timedelta(days=400)
        await repo.insert_event(
            _make_event(event_type=EventType.SERVICE_FAIL, timestamp=very_old)
        )
        # Default 365 day critical retention — 400 day old event should be cleaned
        deleted = await repo.cleanup_old_events(retention_days=30, critical_retention_days=365)
        assert deleted == 1

    async def test_cleanup_critical_within_cap_preserved(self, repo: EventRepository):
        """SERVICE_FAIL events within critical retention window are kept."""
        recent_old = datetime.now(timezone.utc) - timedelta(days=60)
        await repo.insert_event(
            _make_event(event_type=EventType.SERVICE_FAIL, timestamp=recent_old)
        )
        deleted = await repo.cleanup_old_events(retention_days=30, critical_retention_days=365)
        assert deleted == 0

    async def test_get_events_since_with_limit(self, repo: EventRepository):
        now = datetime.now(timezone.utc)
        for i in range(10):
            await repo.insert_event(_make_event(timestamp=now, source=f"src-{i}"))
        since = now - timedelta(minutes=1)
        events = await repo.get_events_since(since, limit=3)
        assert len(events) == 3


# --- Async Context Manager ---


class TestContextManager:
    async def test_async_with(self, tmp_path: Path):
        async with Database(str(tmp_path / "ctx.db")) as db:
            tables = await db.conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            assert len(tables) > 0
        # After exit, conn should be None
        assert db._conn is None

    async def test_context_manager_closes_on_error(self, tmp_path: Path):
        with pytest.raises(RuntimeError):
            async with Database(str(tmp_path / "err.db")) as db:
                raise RuntimeError("simulated error")
        assert db._conn is None
