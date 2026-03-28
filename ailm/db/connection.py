"""Async-compatible SQLite connection manager with WAL mode."""

import asyncio
import logging
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class _AsyncSQLiteConnection:
    """Provide the subset of the ``aiosqlite`` API that ailm uses."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def row_factory(self) -> type[sqlite3.Row] | None:
        """Return the configured row factory."""
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, factory: type[sqlite3.Row] | None) -> None:
        """Set the connection row factory."""
        self._connection.row_factory = factory

    async def execute(
        self, sql: str, parameters: Iterable[Any] | None = None
    ) -> sqlite3.Cursor:
        """Execute a SQL statement in a worker thread."""
        if parameters is None:
            parameters = ()
        return await asyncio.to_thread(self._connection.execute, sql, tuple(parameters))

    async def execute_fetchall(
        self, sql: str, parameters: Iterable[Any] | None = None
    ) -> list[sqlite3.Row]:
        """Execute a query and return all rows in a worker thread."""
        if parameters is None:
            parameters = ()
        def _run():
            return self._connection.execute(sql, tuple(parameters)).fetchall()
        return await asyncio.to_thread(_run)

    async def executescript(self, sql_script: str) -> sqlite3.Cursor:
        """Execute a SQL script in a worker thread."""
        return await asyncio.to_thread(self._connection.executescript, sql_script)

    async def commit(self) -> None:
        """Commit the current transaction in a worker thread."""
        await asyncio.to_thread(self._connection.commit)

    async def close(self) -> None:
        """Close the underlying sqlite connection."""
        self._connection.close()


class Database:
    """Manage application database lifecycle and schema initialization."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: _AsyncSQLiteConnection | None = None

    @property
    def conn(self) -> _AsyncSQLiteConnection:
        """Return the active connection wrapper."""
        if self._conn is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._conn

    async def __aenter__(self) -> "Database":
        """Open the database when entering an async context manager."""
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the database when exiting an async context manager."""
        await self.close()

    async def connect(self) -> None:
        """Open the database connection and ensure the schema exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn = _AsyncSQLiteConnection(connection)
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._init_schema()
        logger.info("Database connected: %s", self.db_path)

    async def close(self) -> None:
        """Close the database if it is open."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _init_schema(self) -> None:
        """Create the schema and seed the schema version row."""
        schema_sql = _SCHEMA_PATH.read_text()
        await self.conn.executescript(schema_sql)

        row = await self.conn.execute_fetchall("SELECT version FROM schema_version")
        if not row:
            await self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            await self.conn.commit()
        else:
            current_version = row[0]["version"]
            if current_version < 2:
                await self._migrate_v2()
                await self.conn.execute(
                    "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
                )
                await self.conn.commit()
                logger.info("Database migrated to schema version %d", SCHEMA_VERSION)

    async def _migrate_v2(self) -> None:
        """Add summary_hash column for post-LLM dedup."""
        cols = await self.conn.execute_fetchall("PRAGMA table_info(events)")
        col_names = {c["name"] for c in cols}
        if "summary_hash" not in col_names:
            await self.conn.execute("ALTER TABLE events ADD COLUMN summary_hash TEXT")
            await self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_summary_hash ON events(summary_hash)"
            )
