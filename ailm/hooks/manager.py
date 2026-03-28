"""HookManager — orchestrates plugin registration and hook dispatch."""

import logging

import pluggy

from ailm.core.models import SystemEvent, SystemStatus
from ailm.hooks.specs import AilmHookSpec

logger = logging.getLogger(__name__)


class HookManager:
    """Central registry for pluggy-based hook plugins."""

    def __init__(self) -> None:
        self.pm = pluggy.PluginManager("ailm")
        self.pm.add_hookspecs(AilmHookSpec)

    def register(self, plugin: object) -> None:
        """Register a plugin instance."""
        self.pm.register(plugin)

    def unregister(self, plugin: object) -> None:
        """Unregister a previously registered plugin."""
        if self.pm.get_name(plugin) is None:
            return
        self.pm.unregister(plugin)

    def fire_event(self, event: SystemEvent) -> None:
        """Dispatch on_event to all registered plugins."""
        self.pm.hook.on_event(event=event)

    def fire_status_change(self, old: SystemStatus, new: SystemStatus) -> None:
        """Dispatch on_status_change to all registered plugins."""
        self.pm.hook.on_status_change(old=old, new=new)

    def fire_action_requested(self, action: str, params: dict) -> bool:
        """Dispatch on_action_requested. Any single False vetoes the action."""
        results: list[bool] = self.pm.hook.on_action_requested(
            action=action, params=params,
        )
        return all(r is not False for r in results)

    def fire_startup(self) -> None:
        """Dispatch on_startup to all registered plugins."""
        self.pm.hook.on_startup()

    def fire_shutdown(self) -> None:
        """Dispatch on_shutdown to all registered plugins."""
        self.pm.hook.on_shutdown()
