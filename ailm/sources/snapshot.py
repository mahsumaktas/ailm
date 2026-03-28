"""Snapper snapshot watcher — publishes SNAPSHOT events on new snapshots."""

import logging
from pathlib import Path

from watchdog.events import DirCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import WatchdogSource

logger = logging.getLogger(__name__)


_SNAPSHOT_WARN_COUNT = 50


class SnapshotSource(WatchdogSource):
    """Watch a snapper directory and emit events for new snapshots."""

    name = "snapshot"

    def __init__(self, snapshot_path: str, warn_count: int = _SNAPSHOT_WARN_COUNT) -> None:
        super().__init__()
        self._snapshot_path = snapshot_path
        self._seen_snapshots: set[str] = set()
        self._warn_count = warn_count
        self._warned = False

    def _setup_observer(self) -> Observer:
        if not Path(self._snapshot_path).is_dir():
            logger.warning("Snapshot path %s not found, source disabled", self._snapshot_path)
            obs = Observer()
            return obs

        handler = _SnapshotHandler(self)
        obs = Observer()
        obs.schedule(handler, self._snapshot_path, recursive=False)
        return obs

    async def on_snapshot(self, dir_name: str) -> None:
        """Handle a new snapshot directory. Public for testing."""
        if not dir_name.isdigit():
            return

        if dir_name in self._seen_snapshots:
            return
        self._seen_snapshots.add(dir_name)

        event = SystemEvent(
            type=EventType.SNAPSHOT,
            severity=Severity.INFO,
            raw_data=f"snapshot={dir_name} path={self._snapshot_path}/{dir_name}",
            source=self.name,
            summary=f"Snapshot #{dir_name} created",
        )
        await self.bus.publish(event)

        # Warn if too many snapshots accumulating
        total = len(self._seen_snapshots)
        if total >= self._warn_count and not self._warned:
            self._warned = True
            await self.bus.publish(SystemEvent(
                type=EventType.DISK_ALERT,
                severity=Severity.WARNING,
                raw_data=f"snapshot_count={total} threshold={self._warn_count}",
                source=self.name,
                summary=f"{total} snapshots accumulated — consider cleanup",
            ))


class _SnapshotHandler(FileSystemEventHandler):
    def __init__(self, source: SnapshotSource) -> None:
        self._source = source

    def on_created(self, event: DirCreatedEvent) -> None:
        if not event.is_directory:
            return
        dir_name = Path(event.src_path).name
        self._source._schedule_async(lambda: self._source.on_snapshot(dir_name))
