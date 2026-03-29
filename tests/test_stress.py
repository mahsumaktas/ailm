"""Stress tests — high volume, concurrency, edge cases across all modules."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.db.connection import Database
from ailm.db.repository import EventRepository
from ailm.llm.queue import LLMTask, LLMTaskQueue
from ailm.sources.journald import JournalEntry, JournaldSource


def _make_event(
    event_type: EventType = EventType.DISK_ALERT,
    severity: Severity = Severity.WARNING,
    source: str = "stress",
) -> SystemEvent:
    return SystemEvent(
        type=event_type, severity=severity,
        raw_data="stress test", source=source,
    )


# --- EventBus stress ---


class TestBusStress:
    async def test_high_volume_events(self):
        """Bus handles 10K events without dropping (queue drains between publishes)."""
        bus = EventBus(maxsize=1000)
        received: list[SystemEvent] = []
        bus.subscribe(None, received.append)
        await bus.start()

        for i in range(10_000):
            await bus.publish(_make_event(source=f"src-{i}"))
            # yield to let dispatcher drain
            if i % 100 == 99:
                await asyncio.sleep(0)

        await bus.stop()
        assert len(received) == 10_000

    async def test_multiple_event_types_concurrent(self):
        """Multiple typed subscribers all receive correct events."""
        bus = EventBus()
        disk: list[SystemEvent] = []
        service: list[SystemEvent] = []
        wild: list[SystemEvent] = []

        bus.subscribe(EventType.DISK_ALERT, disk.append)
        bus.subscribe(EventType.SERVICE_FAIL, service.append)
        bus.subscribe(None, wild.append)
        await bus.start()

        for i in range(500):
            await bus.publish(_make_event(EventType.DISK_ALERT))
            await bus.publish(_make_event(EventType.SERVICE_FAIL))
            if i % 50 == 49:
                await asyncio.sleep(0)
        await bus.stop()

        assert len(disk) == 500
        assert len(service) == 500
        assert len(wild) == 1000

    async def test_bus_stop_with_full_queue(self):
        """stop() doesn't deadlock even when queue is full."""
        bus = EventBus(maxsize=5)
        # Fill the queue without starting dispatcher
        for _ in range(5):
            await bus.publish(_make_event())
        assert bus.pending == 5

        # Start and immediately stop — sentinel uses put_nowait fallback
        await bus.start()
        await bus.stop()

    async def test_rapid_subscribe_unsubscribe(self):
        """Rapid subscribe/unsubscribe doesn't crash dispatch."""
        bus = EventBus()
        await bus.start()

        for _ in range(1000):
            cb = lambda e: None
            bus.subscribe(None, cb)
            await bus.publish(_make_event())
            bus.unsubscribe(None, cb)

        await bus.stop()


# --- DB stress ---


class TestDBStress:
    async def test_bulk_insert(self, tmp_path: Path):
        """Insert 1000 events and verify all retrievable."""
        async with Database(str(tmp_path / "stress.db")) as db:
            repo = EventRepository(db)
            ts = datetime.now(timezone.utc)

            for i in range(1000):
                await repo.insert_event(SystemEvent(
                    type=EventType.LOG_ANOMALY, severity=Severity.INFO,
                    raw_data=f"entry-{i}", source="stress", timestamp=ts,
                ))

            events = await repo.get_events_since(ts - timedelta(minutes=1), limit=2000)
            assert len(events) == 1000

    async def test_concurrent_read_write(self, tmp_path: Path):
        """Read while inserting doesn't crash (WAL mode)."""
        async with Database(str(tmp_path / "concurrent.db")) as db:
            repo = EventRepository(db)

            # Insert some baseline events
            ts = datetime.now(timezone.utc)
            for i in range(100):
                await repo.insert_event(_make_event(source=f"init-{i}"))

            # Interleave reads and writes
            for i in range(100):
                await repo.insert_event(_make_event(source=f"new-{i}"))
                events = await repo.get_recent_events(limit=10)
                assert len(events) == 10

    async def test_cleanup_large_dataset(self, tmp_path: Path):
        """Cleanup on 1000 events performs correctly."""
        async with Database(str(tmp_path / "cleanup.db")) as db:
            repo = EventRepository(db)
            old = datetime.now(timezone.utc) - timedelta(days=60)
            recent = datetime.now(timezone.utc)

            for i in range(500):
                await repo.insert_event(SystemEvent(
                    type=EventType.DISK_ALERT, severity=Severity.WARNING,
                    raw_data=f"old-{i}", source="stress", timestamp=old,
                ))
            for i in range(500):
                await repo.insert_event(SystemEvent(
                    type=EventType.DISK_ALERT, severity=Severity.WARNING,
                    raw_data=f"recent-{i}", source="stress", timestamp=recent,
                ))

            deleted = await repo.cleanup_old_events(retention_days=30)
            assert deleted == 500
            remaining = await repo.get_recent_events(limit=1000)
            assert len(remaining) == 500

    async def test_corrupted_row_skipped(self, tmp_path: Path):
        """Rows with invalid enum values are skipped, not crash."""
        async with Database(str(tmp_path / "corrupt.db")) as db:
            # Insert a valid event
            repo = EventRepository(db)
            await repo.insert_event(_make_event())

            # Manually insert a corrupted row
            await db.conn.execute(
                "INSERT INTO events (timestamp, type, severity, raw_data, source) "
                "VALUES (?, 'INVALID_TYPE', 'info', 'corrupt', 'test')",
                (datetime.now(timezone.utc).isoformat(),),
            )
            await db.conn.commit()

            events = await repo.get_recent_events(limit=10)
            assert len(events) == 1  # only valid event returned


# --- LLM Queue stress ---


class TestQueueStress:
    def test_queue_overflow_evicts_oldest(self):
        """Queue at maxlen evicts oldest entries correctly."""
        q = LLMTaskQueue(maxlen=100)
        for i in range(200):
            q.enqueue(LLMTask(prompt=f"task-{i}"))

        assert q.pending == 100
        # Oldest surviving should be task-100
        assert q._tasks[0].prompt == "task-100"

    def test_queue_rapid_enqueue(self):
        """10K rapid enqueues don't crash."""
        q = LLMTaskQueue(maxlen=500)
        for i in range(10_000):
            q.enqueue(LLMTask(prompt=f"task-{i}"))
        assert q.pending == 500


# --- Journald stress ---


class TestJournaldStress:
    async def test_large_buffer_flush(self):
        """Flush 5000 entries without data loss."""
        bus = EventBus(maxsize=10_000)
        events: list[SystemEvent] = []
        bus.subscribe(None, events.append)
        await bus.start()

        source = JournaldSource()
        source._bus = bus
        ts = datetime.now(timezone.utc)
        for i in range(5000):
            source._buffer.append(JournalEntry(
                message=f"error-{i}", unit="test", priority=3, timestamp=ts,
            ))

        await source._flush_buffer()
        # yield to let bus dispatch
        for _ in range(50):
            await asyncio.sleep(0)
        await bus.stop()

        assert len(events) == 5000

    # Prefilter removed in v0.3 — priority-based filtering only


# --- Source lifecycle stress ---


class TestSourceLifecycleStress:
    async def test_rapid_start_stop(self):
        """Sources can be started and stopped rapidly without leaking."""
        from ailm.sources.reboot import RebootSource

        bus = EventBus()
        await bus.start()

        for _ in range(50):
            source = RebootSource(interval=60, sentinel_path="/tmp/nonexistent")
            await source.start(bus)
            await source.stop()

        await bus.stop()

    async def test_bus_survives_source_errors(self):
        """Bus continues working even if a subscriber callback raises."""
        bus = EventBus()
        good: list[SystemEvent] = []
        error_count = 0

        def bad_sub(event: SystemEvent) -> None:
            nonlocal error_count
            error_count += 1
            raise RuntimeError("boom")

        bus.subscribe(None, bad_sub)
        bus.subscribe(None, good.append)
        await bus.start()

        for _ in range(100):
            await bus.publish(_make_event())
        await bus.stop()

        assert error_count == 100
        assert len(good) == 100


# --- Integration: end-to-end flow ---


class TestEndToEnd:
    @pytest.mark.skip(reason="DiskMonitor replaced by MetricsCollector in v0.3")
    async def test_source_to_db_flow(self, tmp_path: Path):
        """Event published by source → bus → subscriber inserts into DB."""
        from collections import namedtuple
        from ailm.sources.metrics import MetricsCollector as DiskMonitor  # noqa

        DiskUsage = namedtuple("DiskUsage", ["total", "used", "free", "percent"])

        async with Database(str(tmp_path / "e2e.db")) as db:
            repo = EventRepository(db)
            bus = EventBus()

            async def on_event(event: SystemEvent) -> None:
                await repo.insert_event(event)

            bus.subscribe(None, on_event)
            await bus.start()

            monitor = DiskMonitor(warn_pct=80, critical_pct=95, interval=60)
            await monitor.start(bus)

            usage = DiskUsage(total=500_000_000_000, used=425_000_000_000,
                              free=75_000_000_000, percent=85.0)
            with patch("ailm.sources.disk.psutil.disk_usage", return_value=usage):
                await monitor.check()

            await monitor.stop()
            # yield for dispatch
            await asyncio.sleep(0.01)
            await bus.stop()

            events = await repo.get_recent_events()
            assert len(events) == 1
            assert events[0].type == EventType.DISK_ALERT
            assert events[0].severity == Severity.WARNING
            assert events[0].source == "disk"
