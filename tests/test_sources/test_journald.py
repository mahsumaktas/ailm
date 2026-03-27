"""Journald source tests — pre-filter, priority mapping, batcher."""

from datetime import datetime, timezone

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.journald import (
    JournalEntry,
    JournaldSource,
    matches_prefilter,
    priority_to_severity,
)


# --- Pre-filter ---


class TestPrefilter:
    def test_matches_error(self):
        assert matches_prefilter("kernel: EXT4-fs error (device sda1)")

    def test_matches_segfault(self):
        assert matches_prefilter("traps: app[1234] segfault at 0000000000")

    def test_matches_oom(self):
        assert matches_prefilter("Out of memory: Killed process 5678")

    def test_matches_failed(self):
        assert matches_prefilter("systemd[1]: bluetooth.service: Failed with result")

    def test_matches_timeout(self):
        assert matches_prefilter("Connection timeout after 30s")

    def test_matches_denied(self):
        assert matches_prefilter("Permission denied for user admin")

    def test_matches_case_insensitive(self):
        assert matches_prefilter("CRITICAL: disk full")
        assert matches_prefilter("Fatal signal received")

    def test_rejects_normal_log(self):
        assert not matches_prefilter("Started Session 42 of user mahsum")

    def test_rejects_empty(self):
        assert not matches_prefilter("")

    def test_rejects_routine_log(self):
        assert not matches_prefilter("DHCP lease renewed for 192.168.1.100")

    def test_word_boundary_start(self):
        """Leading word boundary: 'error' doesn't match inside 'perror'."""
        assert matches_prefilter("disk I/O error detected")
        assert not matches_prefilter("function perror called")

    def test_matches_word_variants(self):
        """Pre-filter catches word variants like 'failed', 'failure', 'errors'."""
        assert matches_prefilter("Service failed to start")
        assert matches_prefilter("Authentication failure")
        assert matches_prefilter("Multiple errors found")


# --- Priority mapping ---


class TestPriorityMapping:
    def test_emerg_is_critical(self):
        assert priority_to_severity(0) == Severity.CRITICAL

    def test_alert_is_critical(self):
        assert priority_to_severity(1) == Severity.CRITICAL

    def test_err_is_critical(self):
        assert priority_to_severity(3) == Severity.CRITICAL

    def test_warning_is_warning(self):
        assert priority_to_severity(4) == Severity.WARNING

    def test_notice_is_info(self):
        assert priority_to_severity(5) == Severity.INFO

    def test_info_is_info(self):
        assert priority_to_severity(6) == Severity.INFO

    def test_debug_is_info(self):
        assert priority_to_severity(7) == Severity.INFO

    def test_unknown_priority_defaults_info(self):
        assert priority_to_severity(99) == Severity.INFO


# --- Batcher (flush_buffer) ---


class TestBatcher:
    async def test_flush_empty_buffer(self, bus, events):
        source = JournaldSource()
        source._bus = bus
        await bus.start()

        await source._flush_buffer()
        await bus.stop()
        assert len(events) == 0

    async def test_flush_publishes_events(self, bus, events):
        source = JournaldSource()
        source._bus = bus
        await bus.start()

        ts = datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)
        source._buffer.append(JournalEntry(
            message="segfault at 0x0",
            unit="app.service",
            priority=3,
            timestamp=ts,
        ))
        source._buffer.append(JournalEntry(
            message="OOM killed process 42",
            unit="kernel",
            priority=0,
            timestamp=ts,
        ))

        await source._flush_buffer()
        await bus.stop()

        assert len(events) == 2
        assert events[0].type == EventType.LOG_ANOMALY
        assert events[0].severity == Severity.CRITICAL  # priority 3
        assert events[0].source == "journald"
        assert "segfault" in events[0].raw_data
        assert events[1].severity == Severity.CRITICAL  # priority 0

    async def test_flush_clears_buffer(self, bus, events):
        source = JournaldSource()
        source._bus = bus
        await bus.start()

        source._buffer.append(JournalEntry(
            message="error", unit="test", priority=3,
            timestamp=datetime.now(timezone.utc),
        ))
        await source._flush_buffer()
        assert len(source._buffer) == 0

        # Second flush should produce nothing
        await source._flush_buffer()
        await bus.stop()
        assert len(events) == 1

    async def test_buffer_maxlen(self):
        source = JournaldSource()
        ts = datetime.now(timezone.utc)
        for i in range(6000):
            source._buffer.append(JournalEntry(
                message=f"msg-{i}", unit="test", priority=6, timestamp=ts,
            ))
        # maxlen=5000 should cap it
        assert len(source._buffer) == 5000

    async def test_event_timestamp_preserved(self, bus, events):
        source = JournaldSource()
        source._bus = bus
        await bus.start()

        ts = datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)
        source._buffer.append(JournalEntry(
            message="error", unit="test", priority=4, timestamp=ts,
        ))
        await source._flush_buffer()
        await bus.stop()

        assert events[0].timestamp == ts
        assert events[0].severity == Severity.WARNING  # priority 4


# --- Lifecycle ---


class TestJournaldLifecycle:
    async def test_flush_before_start_raises(self):
        source = JournaldSource()
        source._buffer.append(JournalEntry(
            message="error", unit="test", priority=3,
            timestamp=datetime.now(timezone.utc),
        ))
        with pytest.raises(RuntimeError, match="not started"):
            await source._flush_buffer()

    async def test_start_without_systemd_is_noop(self, bus):
        """When python-systemd is not installed, start() logs warning and returns."""
        from unittest.mock import patch
        with patch("ailm.sources.journald.HAS_SYSTEMD", False):
            source = JournaldSource()
            await source.start(bus)
            assert source._reader_task is None
            assert source._batcher_task is None

    async def test_stop_without_start_is_noop(self):
        source = JournaldSource()
        await source.stop()  # should not raise

    async def test_stop_flushes_remaining(self, bus, events):
        """stop() flushes buffer even without reader/batcher running."""
        source = JournaldSource()
        source._bus = bus
        await bus.start()

        source._buffer.append(JournalEntry(
            message="leftover error", unit="test", priority=3,
            timestamp=datetime.now(timezone.utc),
        ))
        await source.stop()
        await bus.stop()
        assert len(events) == 1

    def test_invalid_batch_seconds(self):
        with pytest.raises(ValueError, match="positive"):
            JournaldSource(batch_seconds=0)
        with pytest.raises(ValueError, match="positive"):
            JournaldSource(batch_seconds=-1)
