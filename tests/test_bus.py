"""EventBus and core models tests."""

import asyncio
from datetime import datetime, timezone

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent, SystemStatus


# --- Helpers ---


def _make_event(
    event_type: EventType = EventType.DISK_ALERT,
    severity: Severity = Severity.WARNING,
    source: str = "test",
) -> SystemEvent:
    return SystemEvent(
        type=event_type,
        severity=severity,
        raw_data="test data",
        source=source,
    )


# --- SystemEvent dataclass ---


class TestSystemEvent:
    def test_required_fields(self):
        event = SystemEvent(
            type=EventType.DISK_ALERT,
            severity=Severity.WARNING,
            raw_data="disk at 82%",
            source="psutil",
        )
        assert event.type == EventType.DISK_ALERT
        assert event.severity == Severity.WARNING
        assert event.raw_data == "disk at 82%"
        assert event.source == "psutil"

    def test_auto_timestamp(self):
        before = datetime.now(timezone.utc)
        event = _make_event()
        after = datetime.now(timezone.utc)
        assert before <= event.timestamp <= after

    def test_optional_fields_default_none(self):
        event = _make_event()
        assert event.id is None
        assert event.summary is None
        assert event.user_action is None
        assert event.embedding is None

    def test_all_fields_populated(self):
        ts = datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)
        event = SystemEvent(
            type=EventType.BRIEFING,
            severity=Severity.INFO,
            raw_data="raw",
            source="scheduler",
            timestamp=ts,
            id=42,
            summary="All good",
            user_action="read",
            embedding=b"\x00\x01",
        )
        assert event.id == 42
        assert event.summary == "All good"
        assert event.timestamp == ts
        assert event.embedding == b"\x00\x01"


# --- Enums ---


class TestEnums:
    def test_event_type_values(self):
        assert len(EventType) == 11
        assert EventType.PACKAGE_UPDATE.value == "package_update"
        assert EventType.SERVICE_FAIL.value == "service_fail"
        assert EventType.DISK_ALERT.value == "disk_alert"

    def test_severity_values(self):
        assert len(Severity) == 3
        assert Severity.INFO.value == "info"
        assert Severity.WARNING.value == "warning"
        assert Severity.CRITICAL.value == "critical"

    def test_system_status_values(self):
        assert len(SystemStatus) == 3
        assert SystemStatus.HEALTHY.value == "healthy"
        assert SystemStatus.DEGRADED.value == "degraded"
        assert SystemStatus.CRITICAL.value == "critical"

    def test_str_enum_comparison(self):
        """str enums can be compared with plain strings."""
        assert EventType.DISK_ALERT == "disk_alert"
        assert Severity.CRITICAL == "critical"


# --- EventBus pub/sub ---


class TestPubSub:
    async def test_wildcard_receives_all(self):
        bus = EventBus()
        received: list[SystemEvent] = []
        bus.subscribe(None, received.append)
        await bus.start()

        await bus.publish(_make_event(EventType.DISK_ALERT))
        await bus.publish(_make_event(EventType.SERVICE_FAIL))
        await bus.stop()

        assert len(received) == 2
        assert received[0].type == EventType.DISK_ALERT
        assert received[1].type == EventType.SERVICE_FAIL

    async def test_type_filter(self):
        bus = EventBus()
        disk_events: list[SystemEvent] = []
        bus.subscribe(EventType.DISK_ALERT, disk_events.append)
        await bus.start()

        await bus.publish(_make_event(EventType.DISK_ALERT))
        await bus.publish(_make_event(EventType.SERVICE_FAIL))
        await bus.publish(_make_event(EventType.DISK_ALERT))
        await bus.stop()

        assert len(disk_events) == 2
        assert all(e.type == EventType.DISK_ALERT for e in disk_events)

    async def test_type_and_wildcard_both_called(self):
        """A typed subscriber AND a wildcard subscriber both receive the event."""
        bus = EventBus()
        typed: list[SystemEvent] = []
        wild: list[SystemEvent] = []
        bus.subscribe(EventType.DISK_ALERT, typed.append)
        bus.subscribe(None, wild.append)
        await bus.start()

        await bus.publish(_make_event(EventType.DISK_ALERT))
        await bus.stop()

        assert len(typed) == 1
        assert len(wild) == 1

    async def test_multiple_subscribers_same_type(self):
        bus = EventBus()
        a: list[SystemEvent] = []
        b: list[SystemEvent] = []
        bus.subscribe(EventType.BRIEFING, a.append)
        bus.subscribe(EventType.BRIEFING, b.append)
        await bus.start()

        await bus.publish(_make_event(EventType.BRIEFING))
        await bus.stop()

        assert len(a) == 1
        assert len(b) == 1

    async def test_async_callback(self):
        bus = EventBus()
        received: list[SystemEvent] = []

        async def handler(event: SystemEvent) -> None:
            received.append(event)

        bus.subscribe(None, handler)
        await bus.start()
        await bus.publish(_make_event())
        await bus.stop()

        assert len(received) == 1

    async def test_mixed_sync_async_callbacks(self):
        bus = EventBus()
        sync_received: list[SystemEvent] = []
        async_received: list[SystemEvent] = []

        async def async_handler(event: SystemEvent) -> None:
            async_received.append(event)

        bus.subscribe(None, sync_received.append)
        bus.subscribe(None, async_handler)
        await bus.start()

        await bus.publish(_make_event())
        await bus.stop()

        assert len(sync_received) == 1
        assert len(async_received) == 1

    async def test_no_subscribers_no_error(self):
        bus = EventBus()
        await bus.start()
        await bus.publish(_make_event())  # no subscribers — should not crash
        await bus.stop()

    async def test_unsubscribe(self):
        bus = EventBus()
        received: list[SystemEvent] = []
        bus.subscribe(None, received.append)
        bus.unsubscribe(None, received.append)
        await bus.start()

        await bus.publish(_make_event())
        await bus.stop()

        assert len(received) == 0

    async def test_unsubscribe_nonexistent_is_noop(self):
        bus = EventBus()
        bus.unsubscribe(None, lambda e: None)

    async def test_event_ordering_preserved(self):
        bus = EventBus()
        sources: list[str] = []
        bus.subscribe(None, lambda e: sources.append(e.source))
        await bus.start()

        for i in range(10):
            await bus.publish(_make_event(source=f"src-{i}"))
        await bus.stop()

        assert sources == [f"src-{i}" for i in range(10)]


# --- Backpressure ---


class TestBackpressure:
    async def test_full_queue_drops_event(self):
        bus = EventBus(maxsize=2)
        await bus.publish(_make_event())
        await bus.publish(_make_event())
        await bus.publish(_make_event())  # dropped

        assert bus.pending == 2

    async def test_custom_maxsize(self):
        bus = EventBus(maxsize=5)
        for _ in range(7):
            await bus.publish(_make_event())
        assert bus.pending == 5


# --- Lifecycle ---


class TestLifecycle:
    async def test_start_stop(self):
        bus = EventBus()
        await bus.start()
        assert bus.running
        await bus.stop()
        assert not bus.running

    async def test_double_start_is_noop(self):
        bus = EventBus()
        await bus.start()
        task1 = bus._task
        await bus.start()
        assert bus._task is task1  # same task, not replaced
        await bus.stop()

    async def test_stop_without_start_is_noop(self):
        bus = EventBus()
        await bus.stop()

    async def test_restart_after_stop(self):
        bus = EventBus()
        received: list[SystemEvent] = []
        bus.subscribe(None, received.append)

        await bus.start()
        await bus.publish(_make_event())
        await bus.stop()
        assert len(received) == 1

        # Restart
        await bus.start()
        assert bus.running
        await bus.publish(_make_event())
        await bus.stop()
        assert len(received) == 2

    async def test_pending_count(self):
        bus = EventBus()
        assert bus.pending == 0
        await bus.publish(_make_event())
        assert bus.pending == 1


# --- Error handling ---


class TestErrorHandling:
    async def test_sync_callback_error_does_not_crash_bus(self):
        bus = EventBus()
        good_events: list[SystemEvent] = []

        def bad_callback(event: SystemEvent) -> None:
            raise RuntimeError("boom")

        bus.subscribe(None, bad_callback)
        bus.subscribe(None, good_events.append)
        await bus.start()

        await bus.publish(_make_event())
        await bus.stop()

        assert len(good_events) == 1

    async def test_async_callback_error_does_not_crash_bus(self):
        bus = EventBus()
        good_events: list[SystemEvent] = []

        async def bad_async(event: SystemEvent) -> None:
            raise ValueError("async boom")

        bus.subscribe(None, bad_async)
        bus.subscribe(None, good_events.append)
        await bus.start()

        await bus.publish(_make_event())
        await bus.stop()

        assert len(good_events) == 1

    async def test_multiple_events_after_error(self):
        """Bus continues processing after a callback error."""
        bus = EventBus()
        received: list[SystemEvent] = []
        call_count = 0

        def sometimes_fails(event: SystemEvent) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first call fails")
            received.append(event)

        bus.subscribe(None, sometimes_fails)
        await bus.start()

        await bus.publish(_make_event())
        await bus.publish(_make_event())
        await bus.publish(_make_event())
        await bus.stop()

        assert len(received) == 2  # 2nd and 3rd succeeded

    async def test_subscribe_during_dispatch_safe(self):
        """Subscribing during callback dispatch doesn't crash (list copy)."""
        bus = EventBus()
        received: list[SystemEvent] = []

        def subscribe_more(event: SystemEvent) -> None:
            bus.subscribe(None, received.append)

        bus.subscribe(None, subscribe_more)
        await bus.start()

        await bus.publish(_make_event())
        # Second event should reach the newly subscribed callback
        await bus.publish(_make_event())
        await bus.stop()

        assert len(received) >= 1
