"""Arch/CachyOS implementation of distro protocols."""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from ailm.distro.protocols import PackageEvent, Snapshot

logger = logging.getLogger(__name__)

_ALPM_RE = re.compile(
    r"\[(.+?)\] \[ALPM\] (upgraded|installed|removed) (.+?) \((.+?)\)"
)


class SystemdInit:
    """InitSystem protocol implementation using systemctl subprocess."""

    async def get_failed_units(self) -> list[str]:
        """Return the names of currently failed systemd units."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-units", "--state=failed", "--no-legend", "--plain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            return []

        units = []
        for line in stdout.decode().strip().splitlines():
            parts = line.split()
            if parts:
                units.append(parts[0])
        return units

    async def restart_unit(self, name: str) -> bool:
        """Restart a systemd unit and report whether the command succeeded."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "restart", name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("Failed to restart %s: %s", name, stderr.decode().strip())
                return False
            return True
        except FileNotFoundError:
            return False


class PacmanBackend:
    """PackageManager protocol implementation for pacman."""

    def parse_log_line(self, line: str) -> PackageEvent | None:
        """Parse a single pacman log line into a normalized package event."""
        m = _ALPM_RE.match(line)
        if not m:
            return None
        ts_str, action, name, version_info = m.groups()
        try:
            timestamp = datetime.fromisoformat(ts_str)
        except ValueError:
            return None

        if action == "upgraded" and " -> " in version_info:
            old, new = version_info.split(" -> ", 1)
        elif action == "installed":
            old, new = None, version_info
        else:  # removed — only old_version, no new_version
            old, new = version_info, None
        return PackageEvent(name=name, action=action, timestamp=timestamp,
                            old_version=old, new_version=new)

    def get_recent_updates(self, since: datetime) -> list[PackageEvent]:
        """Return recent package updates newer than ``since``."""
        return []  # used by future phases — reads log file from disk


class SnapperBackend:
    """SnapshotBackend protocol implementation for snapper."""

    def __init__(self, snapshot_path: str) -> None:
        self._path = Path(snapshot_path)

    def list_recent(self, n: int = 10) -> list[Snapshot]:
        """Return the most recent ``n`` snapshots ordered newest-first."""
        if not self._path.is_dir():
            return []
        # Numeric sort (not string) — "9" < "10" < "100"
        numbered = []
        for d in self._path.iterdir():
            if d.is_dir() and d.name.isdigit():
                numbered.append((int(d.name), d))
        numbered.sort(key=lambda t: t[0], reverse=True)

        snapshots: list[Snapshot] = []
        for num, d in numbered[:n]:
            snapshot = self._parse_info(num, d / "info.xml")
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    def get_latest(self) -> Snapshot | None:
        """Return the newest snapshot if one exists."""
        recent = self.list_recent(1)
        return recent[0] if recent else None

    @staticmethod
    def _parse_info(number: int, info_path: Path) -> Snapshot | None:
        try:
            mtime = info_path.parent.stat().st_mtime
        except OSError:
            return None
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc)

        if not info_path.exists():
            return Snapshot(number=number, snapshot_type="unknown",
                            description="", timestamp=ts)
        try:
            tree = ET.parse(info_path)
            root = tree.getroot()
            snap_type = root.findtext("type", default="single")
            desc = root.findtext("description", default="")
            return Snapshot(number=number, snapshot_type=snap_type,
                            description=desc, timestamp=ts)
        except (OSError, ET.ParseError):
            return Snapshot(number=number, snapshot_type="unknown",
                            description="", timestamp=ts)
