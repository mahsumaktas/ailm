"""System status tracker — computes overall health from recent events."""

import logging
from datetime import datetime, timedelta, timezone

from ailm.core.models import Severity, SystemEvent, SystemStatus

logger = logging.getLogger(__name__)

# Events within this window affect status
STATUS_WINDOW = timedelta(hours=1)


class StatusTracker:
    """Tracks overall system status from event stream."""

    def __init__(self) -> None:
        self._status = SystemStatus.HEALTHY
        self._recent_criticals: list[SystemEvent] = []
        self._recent_warnings: list[SystemEvent] = []
        self._llm_available = True
        self._on_change: list = []

    @property
    def status(self) -> SystemStatus:
        """Return the current aggregate system status."""
        return self._status

    def on_status_change(self, callback) -> None:
        """Register a callback invoked with ``(old, new)`` status pairs."""
        self._on_change.append(callback)

    def set_llm_available(self, available: bool) -> None:
        """Update LLM availability and recompute the overall status."""
        self._llm_available = available
        self._recompute()

    def on_event(self, event: SystemEvent) -> None:
        """Bus subscriber — updates status based on incoming events."""
        now = datetime.now(timezone.utc)
        cutoff = now - STATUS_WINDOW

        if event.severity == Severity.CRITICAL:
            self._recent_criticals.append(event)
        elif event.severity == Severity.WARNING:
            self._recent_warnings.append(event)

        # Prune old entries
        self._recent_criticals = [e for e in self._recent_criticals if e.timestamp > cutoff]
        self._recent_warnings = [e for e in self._recent_warnings if e.timestamp > cutoff]

        self._recompute()

    def prune(self) -> None:
        """Remove stale events from tracking lists. Call periodically."""
        cutoff = datetime.now(timezone.utc) - STATUS_WINDOW
        self._recent_criticals = [e for e in self._recent_criticals if e.timestamp > cutoff]
        self._recent_warnings = [e for e in self._recent_warnings if e.timestamp > cutoff]
        self._recompute()

    def _recompute(self) -> None:
        if self._recent_criticals:
            new = SystemStatus.CRITICAL
        elif self._recent_warnings or not self._llm_available:
            new = SystemStatus.DEGRADED
        else:
            new = SystemStatus.HEALTHY

        if new != self._status:
            old = self._status
            self._status = new
            logger.info("System status: %s → %s", old.value, new.value)
            for cb in self._on_change:
                cb(old, new)
