"""Distro abstraction protocols — duck-typed interfaces for multi-distro support."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass
class PackageEvent:
    name: str
    action: str  # "upgraded", "installed", "removed"
    timestamp: datetime
    old_version: str | None = None
    new_version: str | None = None


@dataclass
class Snapshot:
    number: int
    snapshot_type: str  # "pre", "post", "single"
    description: str
    timestamp: datetime


@runtime_checkable
class PackageManager(Protocol):
    def parse_log_line(self, line: str) -> PackageEvent | None: ...

    def get_recent_updates(self, since: datetime) -> list[PackageEvent]: ...


@runtime_checkable
class SnapshotBackend(Protocol):
    def list_recent(self, n: int = 10) -> list[Snapshot]: ...

    def get_latest(self) -> Snapshot | None: ...


@runtime_checkable
class InitSystem(Protocol):
    async def get_failed_units(self) -> list[str]: ...

    async def restart_unit(self, name: str) -> bool: ...
