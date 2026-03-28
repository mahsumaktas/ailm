"""Pacman log watcher — publishes PACKAGE_UPDATE events from pacman.log."""

import asyncio
import logging
from pathlib import Path

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.distro.arch import PacmanBackend
from ailm.sources.base import WatchdogSource

logger = logging.getLogger(__name__)


def _read_new_lines(log_path: str, file_pos: int) -> tuple[list[str], int]:
    """Read and return log lines appended after ``file_pos``."""
    path = Path(log_path)
    try:
        file_size = path.stat().st_size
        if file_size < file_pos:
            file_pos = 0  # log rotation
        with open(path) as f:
            f.seek(file_pos)
            lines = f.readlines()
            new_pos = f.tell()
        return lines, new_pos
    except FileNotFoundError:
        return [], file_pos


class PacmanSource(WatchdogSource):
    """Watch the pacman log and emit package update events."""

    name = "pacman"

    def __init__(self, log_path: str, backend: PacmanBackend | None = None) -> None:
        super().__init__()
        self._log_path = log_path
        self._backend = backend or PacmanBackend()
        self._file_pos: int = 0

    def _setup_observer(self) -> Observer:
        try:
            self._file_pos = Path(self._log_path).stat().st_size
        except FileNotFoundError:
            self._file_pos = 0

        handler = _LogHandler(self, self._log_path)
        obs = Observer()
        obs.schedule(handler, str(Path(self._log_path).parent), recursive=False)
        return obs

    async def process_new_lines(self) -> list[SystemEvent]:
        """Read and process new log lines. Thread-safe via asyncio.Lock."""
        _ = self.bus  # guard: raises RuntimeError if not started
        async with self._lock:
            lines, new_pos = await asyncio.to_thread(
                _read_new_lines, self._log_path, self._file_pos
            )
            self._file_pos = new_pos

        events = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            pkg = self._backend.parse_log_line(raw)
            if pkg is None:
                continue

            summary = f"{pkg.name} {pkg.action}"
            if pkg.action == "upgraded":
                summary += f" ({pkg.old_version} -> {pkg.new_version})"
            elif pkg.action == "installed":
                summary += f" ({pkg.new_version})"
            elif pkg.action == "removed" and pkg.old_version:
                summary += f" ({pkg.old_version})"

            event = SystemEvent(
                type=EventType.PACKAGE_UPDATE,
                severity=Severity.INFO,
                raw_data=raw,
                source=self.name,
                summary=summary,
                timestamp=pkg.timestamp,
            )
            events.append(event)
            await self.bus.publish(event)

        return events


class _LogHandler(FileSystemEventHandler):
    def __init__(self, source: PacmanSource, watched_path: str) -> None:
        self._source = source
        self._watched_name = Path(watched_path).name

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        if Path(event.src_path).name != self._watched_name:
            return
        self._source._schedule_debounced(self._source.process_new_lines)
