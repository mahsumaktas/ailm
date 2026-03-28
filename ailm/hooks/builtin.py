"""Built-in hook implementations shipped with ailm."""

import logging

from ailm.core.models import SystemEvent, SystemStatus
from ailm.hooks.specs import hookimpl

logger = logging.getLogger(__name__)


class LoggingPlugin:
    """Logs every hook invocation at DEBUG level."""

    @hookimpl
    def on_event(self, event: SystemEvent) -> None:
        """Log every event hook invocation."""
        logger.debug("hook:on_event type=%s severity=%s source=%s",
                      event.type.value, event.severity.value, event.source)

    @hookimpl
    def on_status_change(self, old: SystemStatus, new: SystemStatus) -> None:
        """Log every status-transition hook invocation."""
        logger.debug("hook:on_status_change %s -> %s", old.value, new.value)

    @hookimpl
    def on_action_requested(self, action: str, params: dict) -> bool:
        """Log action requests and allow them by default."""
        logger.debug("hook:on_action_requested action=%s params=%s", action, params)
        return True

    @hookimpl
    def on_startup(self) -> None:
        """Log application startup hooks."""
        logger.debug("hook:on_startup")

    @hookimpl
    def on_shutdown(self) -> None:
        """Log application shutdown hooks."""
        logger.debug("hook:on_shutdown")
