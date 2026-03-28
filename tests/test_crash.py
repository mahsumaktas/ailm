"""Tests for CrashDetector — state file, crash detection, analysis."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ailm.core.crash import CrashDetector
from ailm.core.ringlog import RingBufferLog


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ailm"
    d.mkdir()
    return d


class TestCleanShutdown:
    def test_no_crash_after_clean_stop(self, data_dir: Path):
        det = CrashDetector(data_dir)
        det.on_start()  # writes "booted"
        det.on_stop()   # writes "clean"

        # Second start: should NOT detect crash
        det2 = CrashDetector(data_dir)
        report = det2.on_start()
        assert report is None


class TestCrashDetected:
    def test_crash_detected_when_booted_state(self, data_dir: Path):
        # Simulate crash: write "booted" without clean stop
        state_file = data_dir / "last-state"
        state_file.write_text("booted\n")

        det = CrashDetector(data_dir)
        report = det.on_start()
        assert report is not None
        assert report.detected is True
        assert report.previous_state == "booted"

    def test_crash_analysis_without_ringlog(self, data_dir: Path):
        state_file = data_dir / "last-state"
        state_file.write_text("booted\n")

        det = CrashDetector(data_dir)
        report = det.on_start()
        assert "no ring log" in report.analysis


class TestFirstBoot:
    def test_no_crash_on_first_boot(self, data_dir: Path):
        det = CrashDetector(data_dir)
        report = det.on_start()
        assert report is None  # first boot, state was "unknown"


class TestCrashAnalysis:
    def test_oom_detected_in_log(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        log_dir = tmp_path / "ringlog"

        ringlog = RingBufferLog(log_dir=log_dir, max_lines=1000)
        ringlog.open()
        ts = datetime.now(timezone.utc)
        ringlog.write(ts, "INFO", "journald", "normal operation")
        ringlog.write(ts, "CRITICAL", "journald", "OOM killed process chrome")
        ringlog.write(ts, "CRITICAL", "journald", "kernel panic")
        ringlog.close()

        # Simulate crash
        (data_dir / "last-state").write_text("booted\n")

        # Reopen ringlog for reading
        ringlog2 = RingBufferLog(log_dir=log_dir, max_lines=1000)
        ringlog2.open()

        det = CrashDetector(data_dir, ringlog2)
        report = det.on_start()
        assert report is not None
        assert "OOM" in report.analysis
        assert "panic" in report.analysis.lower() or "segfault" in report.analysis.lower()
        assert "CRITICAL" in report.analysis
        ringlog2.close()


class TestStateFileAtomicity:
    def test_state_file_written(self, data_dir: Path):
        det = CrashDetector(data_dir)
        det.on_start()
        state = (data_dir / "last-state").read_text().strip()
        assert state == "booted"

    def test_clean_state_written(self, data_dir: Path):
        det = CrashDetector(data_dir)
        det.on_start()
        det.on_stop()
        state = (data_dir / "last-state").read_text().strip()
        assert state == "clean"
