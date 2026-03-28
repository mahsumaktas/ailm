"""Crash-resilient ring buffer log with periodic fdatasync.

Inspired by pi-power-guard's RingBufferLog.
Survives sudden power loss — at most sync_interval seconds of data lost
(vs journald's 5 minutes).
"""

import logging
import os
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LINE_LIMIT = 2000  # truncate raw_data to prevent oversized writes


class RingBufferLog:
    """Append-only ring log with automatic rotation and fdatasync."""

    def __init__(
        self,
        log_dir: Path,
        max_lines: int = 50000,
        max_archives: int = 3,
        sync_interval: float = 10.0,
    ) -> None:
        self._log_dir = log_dir
        self._max_lines = max_lines
        self._max_archives = max_archives
        self._sync_interval = sync_interval
        self._fd: int | None = None
        self._line_count: int = 0
        self._lock = threading.Lock()
        self._sync_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def current_path(self) -> Path:
        return self._log_dir / "current.log"

    @property
    def line_count(self) -> int:
        return self._line_count

    def open(self) -> None:
        """Create log directory, open current.log, start sync thread."""
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Count existing lines (for resume after restart)
        if self.current_path.exists():
            with open(self.current_path) as f:
                self._line_count = sum(1 for _ in f)
        else:
            self._line_count = 0

        self._fd = os.open(
            str(self.current_path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )

        self._stop_event.clear()
        self._sync_thread = threading.Thread(
            target=self._sync_loop, daemon=True, name="ringlog-sync",
        )
        self._sync_thread.start()
        logger.info("RingBufferLog opened: %s (%d existing lines)", self._log_dir, self._line_count)

    def close(self) -> None:
        """Stop sync thread, final fdatasync, close fd."""
        self._stop_event.set()
        if self._sync_thread is not None:
            self._sync_thread.join(timeout=self._sync_interval + 2)
            self._sync_thread = None

        if self._fd is not None:
            try:
                os.fdatasync(self._fd)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None
        logger.info("RingBufferLog closed")

    def write(self, timestamp: datetime, level: str, source: str, message: str) -> None:
        """Write a single log line. Thread-safe."""
        if self._fd is None:
            return

        # Truncate message to prevent oversized writes
        if len(message) > _LINE_LIMIT:
            message = message[:_LINE_LIMIT] + "..."

        line = f"{timestamp.isoformat()} {level} {source} {message}\n"
        line_bytes = line.encode()

        with self._lock:
            try:
                os.write(self._fd, line_bytes)
            except OSError:
                logger.warning("RingBufferLog write failed")
                return
            self._line_count += 1

            if self._line_count >= self._max_lines:
                self._rotate()

    def sync_now(self) -> None:
        """Immediate fdatasync — call for CRITICAL events."""
        with self._lock:
            if self._fd is not None:
                try:
                    os.fdatasync(self._fd)
                except OSError:
                    pass

    def read_tail(self, n: int = 200) -> list[str]:
        """Read last N lines from current.log (for crash analysis)."""
        lines: list[str] = []
        # Read from current log
        if self.current_path.exists():
            with open(self.current_path) as f:
                all_lines = f.readlines()
                lines = all_lines[-n:]

        # If not enough, read from newest archive
        if len(lines) < n:
            remaining = n - len(lines)
            archives = self._sorted_archives()
            if archives:
                with open(archives[0]) as f:
                    archive_lines = f.readlines()
                    lines = archive_lines[-remaining:] + lines

        return [line.rstrip("\n") for line in lines]

    def _rotate(self) -> None:
        """Rotate current.log → archive-{timestamp}.log, prune old archives."""
        if self._fd is not None:
            try:
                os.fdatasync(self._fd)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None

        # Rename current to archive
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_name = self._log_dir / f"archive-{ts}.log"
        self.current_path.rename(archive_name)

        # Prune old archives (keep max_archives)
        archives = self._sorted_archives()
        for old_archive in archives[self._max_archives:]:
            old_archive.unlink(missing_ok=True)

        # Open new current.log
        self._fd = os.open(
            str(self.current_path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        self._line_count = 0

    def _sorted_archives(self) -> list[Path]:
        """Return archive files sorted newest first."""
        return sorted(
            self._log_dir.glob("archive-*.log"),
            key=lambda p: p.name,
            reverse=True,
        )

    def _sync_loop(self) -> None:
        """Background thread: fdatasync every sync_interval seconds."""
        while not self._stop_event.wait(self._sync_interval):
            if self._fd is not None:
                with self._lock:
                    try:
                        os.fdatasync(self._fd)
                    except OSError:
                        pass
