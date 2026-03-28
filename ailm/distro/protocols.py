"""Distro abstraction protocols — duck-typed interfaces for multi-distro support."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass
class PackageEvent:
    """Normalized package-management event parsed from distro logs."""

    name: str
    action: str  # "upgraded", "installed", "removed"
    timestamp: datetime
    old_version: str | None = None
    new_version: str | None = None

    def __repr__(self) -> str:
        """Return a concise representation of the package event."""
        return (
            "PackageEvent("
            f"name={self.name!r}, "
            f"action={self.action!r}, "
            f"old_version={self.old_version!r}, "
            f"new_version={self.new_version!r}, "
            f"timestamp={self.timestamp.isoformat()!r})"
        )


@dataclass
class Snapshot:
    """Snapshot metadata exposed by snapshot backends."""

    number: int
    snapshot_type: str  # "pre", "post", "single"
    description: str
    timestamp: datetime

    def __repr__(self) -> str:
        """Return a concise representation of the snapshot metadata."""
        return (
            "Snapshot("
            f"number={self.number!r}, "
            f"snapshot_type={self.snapshot_type!r}, "
            f"description={self.description!r}, "
            f"timestamp={self.timestamp.isoformat()!r})"
        )


@runtime_checkable
class PackageManager(Protocol):
    """Protocol for package-manager integrations."""

    def parse_log_line(self, line: str) -> PackageEvent | None:
        """Parse one package-manager log line into a normalized event."""
        ...

    def get_recent_updates(self, since: datetime) -> list[PackageEvent]:
        """Return package events newer than ``since``."""
        ...


@runtime_checkable
class SnapshotBackend(Protocol):
    """Protocol for snapshot-management integrations."""

    def list_recent(self, n: int = 10) -> list[Snapshot]:
        """Return up to ``n`` recent snapshots ordered newest-first."""
        ...

    def get_latest(self) -> Snapshot | None:
        """Return the newest snapshot, if one exists."""
        ...


@runtime_checkable
class InitSystem(Protocol):
    """Protocol for service-management integrations."""

    async def get_failed_units(self) -> list[str]:
        """Return names of services currently considered failed."""
        ...

    async def restart_unit(self, name: str) -> bool:
        """Restart a service unit and report whether it succeeded."""
        ...
