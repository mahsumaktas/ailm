"""Event repository — CRUD operations on the events table."""

import logging
from datetime import datetime, timedelta, timezone

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.db.connection import Database

logger = logging.getLogger(__name__)

# Columns to fetch (excluding embedding BLOB — only needed for v0.4 similarity search)
_EVENT_COLS = "id, timestamp, type, severity, summary, raw_data, source, user_action"


class EventRepository:
    """Read and write ``SystemEvent`` records in the SQLite database."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def insert_event(self, event: SystemEvent) -> int:
        """Insert an event and update the in-memory object with its row id."""
        cursor = await self.db.conn.execute(
            """INSERT INTO events (timestamp, type, severity, summary, summary_hash, raw_data, source, user_action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.timestamp.isoformat(),
                event.type.value,
                event.severity.value,
                event.summary,
                getattr(event, "summary_hash", None),
                event.raw_data,
                event.source,
                event.user_action,
            ),
        )
        await self.db.conn.commit()
        event.id = cursor.lastrowid
        return cursor.lastrowid

    async def get_events_since(
        self, since: datetime, event_type: EventType | None = None,
        limit: int = 1000,
    ) -> list[SystemEvent]:
        """Return events newer than ``since``, optionally filtered by type."""
        if event_type is not None:
            rows = await self.db.conn.execute_fetchall(
                f"SELECT {_EVENT_COLS} FROM events WHERE timestamp >= ? AND type = ? ORDER BY timestamp LIMIT ?",
                (since.isoformat(), event_type.value, limit),
            )
        else:
            rows = await self.db.conn.execute_fetchall(
                f"SELECT {_EVENT_COLS} FROM events WHERE timestamp >= ? ORDER BY timestamp LIMIT ?",
                (since.isoformat(), limit),
            )
        return [e for r in rows if (e := self._row_to_event(r)) is not None]

    async def get_unanalyzed_since(
        self, since: datetime, limit: int = 50,
    ) -> list[SystemEvent]:
        """Return events with no summary newer than ``since``."""
        rows = await self.db.conn.execute_fetchall(
            f"SELECT {_EVENT_COLS} FROM events WHERE timestamp >= ? AND summary IS NULL ORDER BY timestamp LIMIT ?",
            (since.isoformat(), limit),
        )
        return [e for r in rows if (e := self._row_to_event(r)) is not None]

    async def get_recent_events(self, limit: int = 50) -> list[SystemEvent]:
        """Return the most recent events in reverse chronological order."""
        rows = await self.db.conn.execute_fetchall(
            f"SELECT {_EVENT_COLS} FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        return [e for r in rows if (e := self._row_to_event(r)) is not None]

    async def update_user_action(self, event_id: int, action: str) -> None:
        """Persist the user action chosen for a stored event."""
        await self.db.conn.execute(
            "UPDATE events SET user_action = ? WHERE id = ?", (action, event_id)
        )
        await self.db.conn.commit()

    async def update_summary(
        self, event_id: int, summary: str, summary_hash: str | None = None,
    ) -> None:
        """Update the generated summary and optional hash for a stored event."""
        if summary_hash is not None:
            await self.db.conn.execute(
                "UPDATE events SET summary = ?, summary_hash = ? WHERE id = ?",
                (summary, summary_hash, event_id),
            )
        else:
            await self.db.conn.execute(
                "UPDATE events SET summary = ? WHERE id = ?", (summary, event_id)
            )
        await self.db.conn.commit()

    async def get_event_count_by_type(self, since: datetime) -> dict[str, int]:
        """Return counts grouped by event type for rows newer than ``since``."""
        rows = await self.db.conn.execute_fetchall(
            "SELECT type, COUNT(*) as cnt FROM events WHERE timestamp >= ? GROUP BY type",
            (since.isoformat(),),
        )
        return {row["type"]: row["cnt"] for row in rows}

    async def cleanup_old_events(self, retention_days: int,
                                  critical_retention_days: int = 365) -> int:
        """Delete old events. SERVICE_FAIL and user-applied events get longer retention."""
        normal_cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        critical_cutoff = (datetime.now(timezone.utc) - timedelta(days=critical_retention_days)).isoformat()

        # Delete normal events past retention
        c1 = await self.db.conn.execute(
            """DELETE FROM events
               WHERE timestamp < ?
                 AND type != ?
                 AND (user_action IS NULL OR user_action != 'applied')""",
            (normal_cutoff, EventType.SERVICE_FAIL.value),
        )
        # Delete critical events past extended retention (1yr default)
        c2 = await self.db.conn.execute(
            """DELETE FROM events
               WHERE timestamp < ?
                 AND (type = ? OR user_action = 'applied')""",
            (critical_cutoff, EventType.SERVICE_FAIL.value),
        )
        await self.db.conn.commit()
        return c1.rowcount + c2.rowcount

    @staticmethod
    def _row_to_event(row) -> SystemEvent | None:
        try:
            return SystemEvent(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                type=EventType(row["type"]),
                severity=Severity(row["severity"]),
                summary=row["summary"],
                raw_data=row["raw_data"] or "",
                source=row["source"],
                user_action=row["user_action"],
            )
        except (ValueError, KeyError):
            logger.warning("Skipping corrupted event row id=%s", row["id"])
            return None
