"""Tests for RingBufferLog — write, rotate, read_tail, sync."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ailm.core.ringlog import RingBufferLog


@pytest.fixture
def ringlog(tmp_path: Path):
    log = RingBufferLog(
        log_dir=tmp_path / "ringlog",
        max_lines=100,
        max_archives=2,
        sync_interval=60.0,  # long interval — we test sync_now explicitly
    )
    log.open()
    yield log
    log.close()


class TestWriteAndRead:
    def test_write_creates_file(self, ringlog: RingBufferLog):
        ts = datetime.now(timezone.utc)
        ringlog.write(ts, "INFO", "test", "hello world")
        assert ringlog.current_path.exists()
        assert ringlog.line_count == 1

    def test_write_and_read_tail(self, ringlog: RingBufferLog):
        ts = datetime.now(timezone.utc)
        for i in range(10):
            ringlog.write(ts, "INFO", "test", f"line {i}")
        lines = ringlog.read_tail(10)
        assert len(lines) == 10
        assert "line 0" in lines[0]
        assert "line 9" in lines[9]

    def test_read_tail_fewer_lines(self, ringlog: RingBufferLog):
        ts = datetime.now(timezone.utc)
        ringlog.write(ts, "INFO", "test", "only one")
        lines = ringlog.read_tail(100)
        assert len(lines) == 1

    def test_line_format(self, ringlog: RingBufferLog):
        ts = datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc)
        ringlog.write(ts, "CRITICAL", "journald", "OOM killed chrome")
        lines = ringlog.read_tail(1)
        assert "2026-03-28" in lines[0]
        assert "CRITICAL" in lines[0]
        assert "journald" in lines[0]
        assert "OOM killed chrome" in lines[0]

    def test_truncates_long_message(self, ringlog: RingBufferLog):
        ts = datetime.now(timezone.utc)
        long_msg = "x" * 5000
        ringlog.write(ts, "INFO", "test", long_msg)
        lines = ringlog.read_tail(1)
        assert len(lines[0]) < 5000
        assert "..." in lines[0]


class TestRotation:
    def test_rotates_at_max_lines(self, ringlog: RingBufferLog):
        ts = datetime.now(timezone.utc)
        for i in range(101):
            ringlog.write(ts, "INFO", "test", f"line {i}")
        # After 101 writes with max_lines=100, should have rotated
        assert ringlog.line_count < 100
        archives = list(ringlog._log_dir.glob("archive-*.log"))
        assert len(archives) >= 1

    def test_max_archives_pruned(self, ringlog: RingBufferLog):
        ts = datetime.now(timezone.utc)
        # Force 4 rotations (max_archives=2)
        for rotation in range(4):
            for i in range(100):
                ringlog.write(ts, "INFO", "test", f"r{rotation} line {i}")
        archives = list(ringlog._log_dir.glob("archive-*.log"))
        assert len(archives) <= 2

    def test_read_tail_spans_archive(self, tmp_path: Path):
        log = RingBufferLog(
            log_dir=tmp_path / "ringlog", max_lines=10, max_archives=3
        )
        log.open()
        ts = datetime.now(timezone.utc)
        # Write 15 lines → rotation at 10, then 5 more in new current
        for i in range(15):
            log.write(ts, "INFO", "test", f"line {i}")
        lines = log.read_tail(15)
        assert len(lines) == 15
        log.close()


class TestResumeAfterRestart:
    def test_line_count_preserved(self, tmp_path: Path):
        log_dir = tmp_path / "ringlog"
        log = RingBufferLog(log_dir=log_dir, max_lines=1000)
        log.open()
        ts = datetime.now(timezone.utc)
        for i in range(50):
            log.write(ts, "INFO", "test", f"line {i}")
        log.close()

        # Reopen
        log2 = RingBufferLog(log_dir=log_dir, max_lines=1000)
        log2.open()
        assert log2.line_count == 50
        log2.close()


class TestSyncNow:
    def test_sync_does_not_crash(self, ringlog: RingBufferLog):
        ts = datetime.now(timezone.utc)
        ringlog.write(ts, "CRITICAL", "test", "important")
        ringlog.sync_now()  # should not raise

    def test_sync_on_closed_is_safe(self, tmp_path: Path):
        log = RingBufferLog(log_dir=tmp_path / "ringlog")
        # Not opened — sync should be a no-op
        log.sync_now()
