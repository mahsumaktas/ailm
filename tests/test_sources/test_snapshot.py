"""Snapshot source + backend tests."""

from pathlib import Path

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.distro.arch import SnapperBackend
from ailm.sources.snapshot import SnapshotSource


# --- SnapperBackend ---


class TestSnapperBackend:
    def test_list_recent_empty_dir(self, tmp_path: Path):
        backend = SnapperBackend(str(tmp_path))
        assert backend.list_recent() == []

    def test_list_recent_with_numbered_dirs(self, tmp_path: Path):
        for i in (100, 101, 102):
            (tmp_path / str(i)).mkdir()
        backend = SnapperBackend(str(tmp_path))
        snapshots = backend.list_recent(n=10)
        assert len(snapshots) == 3
        assert snapshots[0].number == 102

    def test_numeric_sort_not_string(self, tmp_path: Path):
        """Ensures 9 < 10 < 100 (numeric), not "100" < "10" < "9" (string)."""
        for i in (9, 10, 100):
            (tmp_path / str(i)).mkdir()
        backend = SnapperBackend(str(tmp_path))
        snapshots = backend.list_recent(n=10)
        assert [s.number for s in snapshots] == [100, 10, 9]

    def test_list_recent_with_info_xml(self, tmp_path: Path):
        d = tmp_path / "50"
        d.mkdir()
        (d / "info.xml").write_text(
            "<snapshot><type>pre</type>"
            "<description>pacman -Syu</description></snapshot>"
        )
        backend = SnapperBackend(str(tmp_path))
        snapshots = backend.list_recent()
        assert len(snapshots) == 1
        assert snapshots[0].snapshot_type == "pre"
        assert snapshots[0].description == "pacman -Syu"

    def test_xml_with_special_chars(self, tmp_path: Path):
        d = tmp_path / "60"
        d.mkdir()
        (d / "info.xml").write_text(
            "<snapshot><type>post</type>"
            "<description>install pkg &amp; deps</description></snapshot>"
        )
        backend = SnapperBackend(str(tmp_path))
        snapshots = backend.list_recent()
        assert snapshots[0].description == "install pkg & deps"

    def test_ignores_non_numeric_dirs(self, tmp_path: Path):
        (tmp_path / "current").mkdir()
        (tmp_path / "42").mkdir()
        backend = SnapperBackend(str(tmp_path))
        snapshots = backend.list_recent()
        assert len(snapshots) == 1
        assert snapshots[0].number == 42

    def test_get_latest(self, tmp_path: Path):
        for i in (10, 20, 30):
            (tmp_path / str(i)).mkdir()
        backend = SnapperBackend(str(tmp_path))
        latest = backend.get_latest()
        assert latest is not None
        assert latest.number == 30

    def test_get_latest_empty(self, tmp_path: Path):
        backend = SnapperBackend(str(tmp_path))
        assert backend.get_latest() is None

    def test_nonexistent_path(self):
        backend = SnapperBackend("/nonexistent/path")
        assert backend.list_recent() == []

    def test_timestamp_is_timezone_aware(self, tmp_path: Path):
        (tmp_path / "1").mkdir()
        backend = SnapperBackend(str(tmp_path))
        snapshots = backend.list_recent()
        assert snapshots[0].timestamp.tzinfo is not None

    def test_missing_info_xml_returns_unknown(self, tmp_path: Path):
        (tmp_path / "5").mkdir()
        backend = SnapperBackend(str(tmp_path))
        snapshots = backend.list_recent()
        assert snapshots[0].snapshot_type == "unknown"


# --- SnapshotSource ---


class TestSnapshotSource:
    async def test_on_snapshot_numeric_dir(self, bus, events):
        source = SnapshotSource("/tmp/fake")
        source._bus = bus
        await bus.start()

        await source.on_snapshot("148")
        await bus.stop()

        assert len(events) == 1
        assert events[0].type == EventType.SNAPSHOT
        assert events[0].severity == Severity.INFO
        assert "#148" in events[0].summary

    async def test_on_snapshot_non_numeric_ignored(self, bus, events):
        source = SnapshotSource("/tmp/fake")
        source._bus = bus
        await bus.start()

        await source.on_snapshot("current")
        await bus.stop()

        assert len(events) == 0

    async def test_on_snapshot_before_start_raises(self):
        source = SnapshotSource("/tmp/fake")
        with pytest.raises(RuntimeError, match="not started"):
            await source.on_snapshot("42")

    async def test_snapshot_dedup(self, bus, events):
        """Same snapshot number not reported twice."""
        source = SnapshotSource("/tmp/fake")
        source._bus = bus
        await bus.start()

        await source.on_snapshot("148")
        await source.on_snapshot("148")
        await source.on_snapshot("149")
        await bus.stop()

        assert len(events) == 2  # 148 once, 149 once

    async def test_source_field_equals_name(self, bus, events):
        source = SnapshotSource("/tmp/fake")
        source._bus = bus
        await bus.start()
        await source.on_snapshot("1")
        await bus.stop()
        assert events[0].source == "snapshot"
