"""Pacman source + backend tests."""

import asyncio
from pathlib import Path

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.distro.arch import PacmanBackend
from ailm.sources.pacman import PacmanSource


def _init_source(source: PacmanSource, bus: EventBus, file_pos: int = 0) -> None:
    """Initialize source internals without starting watchdog observer."""
    source._bus = bus
    source._lock = asyncio.Lock()
    source._file_pos = file_pos


# --- PacmanBackend parsing ---


class TestPacmanBackend:
    def setup_method(self):
        self.backend = PacmanBackend()

    def test_parse_upgraded(self):
        line = "[2026-03-26T10:00:00+0000] [ALPM] upgraded linux (6.17.1-1 -> 6.17.2-1)"
        pkg = self.backend.parse_log_line(line)
        assert pkg is not None
        assert pkg.name == "linux"
        assert pkg.action == "upgraded"
        assert pkg.old_version == "6.17.1-1"
        assert pkg.new_version == "6.17.2-1"

    def test_parse_installed(self):
        line = "[2026-03-26T10:00:00+0000] [ALPM] installed python-pip (23.3-1)"
        pkg = self.backend.parse_log_line(line)
        assert pkg is not None
        assert pkg.name == "python-pip"
        assert pkg.action == "installed"
        assert pkg.old_version is None
        assert pkg.new_version == "23.3-1"

    def test_parse_removed(self):
        line = "[2026-03-26T10:00:00+0000] [ALPM] removed python2 (2.7.18-4)"
        pkg = self.backend.parse_log_line(line)
        assert pkg is not None
        assert pkg.name == "python2"
        assert pkg.action == "removed"
        assert pkg.old_version == "2.7.18-4"
        assert pkg.new_version is None  # removed has no new version

    def test_parse_pacman_line_ignored(self):
        line = "[2026-03-26T10:00:00+0000] [PACMAN] Running 'pacman -Syu'"
        assert self.backend.parse_log_line(line) is None

    def test_parse_empty_line(self):
        assert self.backend.parse_log_line("") is None

    def test_parse_garbage(self):
        assert self.backend.parse_log_line("not a log line") is None

    def test_parse_timestamp(self):
        line = "[2026-03-26T14:30:00+0000] [ALPM] installed foo (1.0-1)"
        pkg = self.backend.parse_log_line(line)
        assert pkg is not None
        assert pkg.timestamp.hour == 14
        assert pkg.timestamp.minute == 30


# --- PacmanSource event generation ---


class TestPacmanSource:
    async def test_process_new_lines(self, tmp_path: Path, bus, events):
        log = tmp_path / "pacman.log"
        log.write_text("")

        source = PacmanSource(str(log))
        await bus.start()
        _init_source(source, bus, file_pos=0)

        log.write_text(
            "[2026-03-26T10:00:00+0000] [ALPM] upgraded mesa (24.0-1 -> 24.1-1)\n"
            "[2026-03-26T10:00:00+0000] [ALPM] installed vulkan-tools (1.3-1)\n"
        )

        result = await source.process_new_lines()
        await bus.stop()

        assert len(result) == 2
        assert len(events) == 2
        assert events[0].type == EventType.PACKAGE_UPDATE
        assert events[0].severity == Severity.INFO
        assert "mesa" in events[0].summary
        assert "upgraded" in events[0].summary

    async def test_skips_non_alpm_lines(self, tmp_path: Path, bus, events):
        log = tmp_path / "pacman.log"
        log.write_text(
            "[2026-03-26T10:00:00+0000] [PACMAN] Running 'pacman -Syu'\n"
            "[2026-03-26T10:00:00+0000] [ALPM] upgraded linux (6.17-1 -> 6.18-1)\n"
        )

        source = PacmanSource(str(log))
        await bus.start()
        _init_source(source, bus, file_pos=0)

        result = await source.process_new_lines()
        await bus.stop()

        assert len(result) == 1
        assert "linux" in result[0].summary

    async def test_seek_reads_only_new(self, tmp_path: Path, bus, events):
        log = tmp_path / "pacman.log"
        log.write_text("[2026-03-26T09:00:00+0000] [ALPM] installed old-pkg (1.0-1)\n")

        source = PacmanSource(str(log))
        await bus.start()
        _init_source(source, bus, file_pos=log.stat().st_size)  # skip existing content

        # Append new line
        with open(log, "a") as f:
            f.write("[2026-03-26T10:00:00+0000] [ALPM] installed new-pkg (2.0-1)\n")

        result = await source.process_new_lines()
        await bus.stop()

        assert len(result) == 1
        assert "new-pkg" in result[0].summary

    async def test_missing_log_file(self, tmp_path: Path, bus, events):
        source = PacmanSource(str(tmp_path / "nonexistent.log"))
        await bus.start()
        _init_source(source, bus)

        result = await source.process_new_lines()
        await bus.stop()
        assert result == []

    async def test_log_rotation_resets_position(self, tmp_path: Path, bus, events):
        """When log is rotated (truncated), _file_pos resets to 0."""
        log = tmp_path / "pacman.log"
        log.write_text(
            "[2026-03-26T09:00:00+0000] [ALPM] installed old-pkg (1.0-1)\n"
            "[2026-03-26T09:01:00+0000] [ALPM] installed another-pkg (2.0-1)\n"
        )

        source = PacmanSource(str(log))
        await bus.start()
        _init_source(source, bus, file_pos=log.stat().st_size)  # at end

        # Simulate log rotation — new smaller file
        log.write_text("[2026-03-26T10:00:00+0000] [ALPM] installed fresh-pkg (3.0-1)\n")

        result = await source.process_new_lines()
        await bus.stop()

        assert len(result) == 1
        assert "fresh-pkg" in result[0].summary

    async def test_process_before_start_raises(self, tmp_path: Path):
        log = tmp_path / "pacman.log"
        log.write_text("[2026-03-26T10:00:00+0000] [ALPM] installed pkg (1.0-1)\n")
        source = PacmanSource(str(log))
        source._file_pos = 0
        with pytest.raises(RuntimeError, match="not started"):
            await source.process_new_lines()
