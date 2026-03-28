"""Core data models used throughout ailm."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class EventType(str, Enum):
    """Kinds of system events ailm can publish."""

    PACKAGE_UPDATE = "package_update"
    SERVICE_FAIL = "service_fail"
    DISK_ALERT = "disk_alert"
    SNAPSHOT = "snapshot"
    LOG_ANOMALY = "log_anomaly"
    REBOOT_REQUIRED = "reboot_required"
    BRIEFING = "briefing"
    SYSTEM_METRIC = "system_metric"
    TREND_ALERT = "trend_alert"
    BOOT_ANALYSIS = "boot_analysis"
    CONFIG_CHANGE = "config_change"


class Severity(str, Enum):
    """Severity levels attached to system events."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SystemStatus(str, Enum):
    """Overall application health states derived from recent events."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"     # LLM unavailable or unresolved warnings
    CRITICAL = "critical"     # disk >95%, unrecovered service failures


@dataclass
class SystemEvent:
    """Single event flowing through the bus, UI, and persistence layers."""

    type: EventType
    severity: Severity
    raw_data: str
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int | None = None
    summary: str | None = None
    user_action: str | None = None
    embedding: bytes | None = None  # v0.4 — sqlite-vec

    def __repr__(self) -> str:
        """Return a compact representation focused on debugging fields."""
        return (
            "SystemEvent("
            f"id={self.id!r}, "
            f"type={self.type.value!r}, "
            f"severity={self.severity.value!r}, "
            f"source={self.source!r}, "
            f"summary={self.summary!r}, "
            f"timestamp={self.timestamp.isoformat()!r})"
        )
