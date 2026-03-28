"""Boot crash detection — state file + previous session analysis.

Inspired by pi-power-guard's CrashDetector.
Detects unclean shutdowns and analyzes the pre-crash ring log.
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ailm.core.ringlog import RingBufferLog

logger = logging.getLogger(__name__)

STATE_FILE_NAME = "last-state"
_CRITICAL_RE = re.compile(r"\bCRITICAL\b")
_OOM_RE = re.compile(r"(?i)\b(oom|out.of.memory|killed.process)\b")
_PANIC_RE = re.compile(r"(?i)\b(panic|segfault|coredump)\b")


@dataclass
class CrashReport:
    """Result of boot-time crash analysis."""

    detected: bool
    previous_state: str
    pre_crash_log: list[str]
    analysis: str


class CrashDetector:
    """Detect and analyze crashes from previous session."""

    def __init__(self, data_dir: Path, ringlog: RingBufferLog | None = None) -> None:
        self._state_path = data_dir / STATE_FILE_NAME
        self._ringlog = ringlog

    def on_start(self) -> CrashReport | None:
        """Check if previous session crashed. Write 'booted' to state file."""
        previous_state = self._read_state()
        crash_detected = previous_state == "booted"

        # Write "booted" atomically
        self._write_state("booted")

        if not crash_detected:
            return None

        # Analyze pre-crash log
        pre_crash_lines: list[str] = []
        if self._ringlog is not None:
            pre_crash_lines = self._ringlog.read_tail(200)

        analysis = self._analyze(pre_crash_lines)
        logger.warning("Previous session crashed: %s", analysis)

        return CrashReport(
            detected=True,
            previous_state=previous_state,
            pre_crash_log=pre_crash_lines,
            analysis=analysis,
        )

    def on_stop(self) -> None:
        """Mark clean shutdown."""
        self._write_state("clean")

    def _read_state(self) -> str:
        """Read previous state file. Returns 'unknown' if missing."""
        try:
            return self._state_path.read_text().strip()
        except FileNotFoundError:
            return "unknown"  # first boot
        except OSError:
            logger.warning("Could not read state file")
            return "unknown"

    def _write_state(self, state: str) -> None:
        """Write state file atomically (write+fsync+rename)."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=self._state_path.parent)
            os.write(fd, (state + "\n").encode())
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.rename(tmp_path, self._state_path)
            tmp_path = None
        except OSError:
            logger.warning("Could not write state file")
        finally:
            if fd is not None:
                os.close(fd)
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _analyze(self, lines: list[str]) -> str:
        """Simple heuristic analysis of pre-crash log lines."""
        if not lines:
            return "Previous session crashed (no ring log available for analysis)"

        critical_count = sum(1 for l in lines if _CRITICAL_RE.search(l))
        oom_match = any(_OOM_RE.search(l) for l in lines)
        panic_match = any(_PANIC_RE.search(l) for l in lines)

        # Find last source
        last_line = lines[-1] if lines else ""
        parts = last_line.split(maxsplit=3)
        last_source = parts[2] if len(parts) >= 3 else "unknown"

        summary_parts = [f"Previous session crashed ({len(lines)} log lines recovered)"]
        if critical_count:
            summary_parts.append(f"{critical_count} CRITICAL events")
        if oom_match:
            summary_parts.append("OOM detected")
        if panic_match:
            summary_parts.append("kernel panic/segfault detected")
        summary_parts.append(f"last active source: {last_source}")

        return ". ".join(summary_parts)
