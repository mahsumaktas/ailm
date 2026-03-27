"""Hook specifications — defines the contract plugins implement."""

import pluggy

from ailm.core.models import SystemEvent, SystemStatus

hookspec = pluggy.HookspecMarker("ailm")
hookimpl = pluggy.HookimplMarker("ailm")


class AilmHookSpec:
    """All hooks a plugin may implement."""

    @hookspec
    def on_event(self, event: SystemEvent) -> None:
        """Called for every event published on the bus."""

    @hookspec
    def on_status_change(self, old: SystemStatus, new: SystemStatus) -> None:
        """Called when overall system status transitions."""

    @hookspec
    def on_action_requested(self, action: str, params: dict) -> bool:
        """Called before executing an action. Return False to veto."""

    @hookspec
    def on_startup(self) -> None:
        """Called once when ailm starts."""

    @hookspec
    def on_shutdown(self) -> None:
        """Called once when ailm stops."""
