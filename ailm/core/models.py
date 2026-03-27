"""Core data models used throughout ailm."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class EventType(str, Enum):
    PACKAGE_UPDATE = "package_update"
    SERVICE_FAIL = "service_fail"
    DISK_ALERT = "disk_alert"
    SNAPSHOT = "snapshot"
    LOG_ANOMALY = "log_anomaly"
    REBOOT_REQUIRED = "reboot_required"
    BRIEFING = "briefing"
    SYSTEM_METRIC = "system_metric"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SystemStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"     # LLM unavailable or unresolved warnings
    CRITICAL = "critical"     # disk >95%, unrecovered service failures


@dataclass
class SystemEvent:
    type: EventType
    severity: Severity
    raw_data: str
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: int | None = None
    summary: str | None = None
    user_action: str | None = None
    embedding: bytes | None = None  # v0.4 — sqlite-vec
