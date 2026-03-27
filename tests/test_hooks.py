"""Tests for the pluggy-based hook system."""

import logging

from ailm.core.models import EventType, Severity, SystemEvent, SystemStatus
from ailm.hooks import HookManager, hookimpl
from ailm.hooks.builtin import LoggingPlugin


def _make_event() -> SystemEvent:
    return SystemEvent(
        type=EventType.DISK_ALERT,
        severity=Severity.WARNING,
        raw_data="disk /dev/sda1 at 92%",
        source="test",
    )


# --- helpers: ad-hoc plugins ------------------------------------------------

class RecorderPlugin:
    """Records every hook call for assertion."""

    def __init__(self) -> None:
        self.events: list[SystemEvent] = []
        self.status_changes: list[tuple[SystemStatus, SystemStatus]] = []
        self.actions: list[tuple[str, dict]] = []
        self.started = False
        self.stopped = False

    @hookimpl
    def on_event(self, event: SystemEvent) -> None:
        self.events.append(event)

    @hookimpl
    def on_status_change(self, old: SystemStatus, new: SystemStatus) -> None:
        self.status_changes.append((old, new))

    @hookimpl
    def on_action_requested(self, action: str, params: dict) -> bool:
        self.actions.append((action, params))
        return True

    @hookimpl
    def on_startup(self) -> None:
        self.started = True

    @hookimpl
    def on_shutdown(self) -> None:
        self.stopped = True


class VetoPlugin:
    """Always vetoes on_action_requested."""

    @hookimpl
    def on_action_requested(self, action: str, params: dict) -> bool:
        return False


# --- tests -------------------------------------------------------------------

def test_register_and_fire_event() -> None:
    hm = HookManager()
    rec = RecorderPlugin()
    hm.register(rec)

    event = _make_event()
    hm.fire_event(event)

    assert len(rec.events) == 1
    assert rec.events[0] is event


def test_action_veto() -> None:
    hm = HookManager()
    rec = RecorderPlugin()  # returns True
    veto = VetoPlugin()     # returns False
    hm.register(rec)
    hm.register(veto)

    allowed = hm.fire_action_requested("reboot", {"reason": "kernel update"})

    assert not allowed
    assert len(rec.actions) == 1


def test_action_allowed_when_no_veto() -> None:
    hm = HookManager()
    rec = RecorderPlugin()
    hm.register(rec)

    allowed = hm.fire_action_requested("update", {"packages": ["linux"]})

    assert allowed
    assert rec.actions == [("update", {"packages": ["linux"]})]


def test_multiple_plugins_all_called() -> None:
    hm = HookManager()
    rec1 = RecorderPlugin()
    rec2 = RecorderPlugin()
    hm.register(rec1)
    hm.register(rec2)

    event = _make_event()
    hm.fire_event(event)

    assert len(rec1.events) == 1
    assert len(rec2.events) == 1


def test_unregister_removes_plugin() -> None:
    hm = HookManager()
    rec = RecorderPlugin()
    hm.register(rec)

    hm.fire_event(_make_event())
    assert len(rec.events) == 1

    hm.unregister(rec)
    hm.fire_event(_make_event())
    assert len(rec.events) == 1  # no new events after unregister


def test_status_change() -> None:
    hm = HookManager()
    rec = RecorderPlugin()
    hm.register(rec)

    hm.fire_status_change(SystemStatus.HEALTHY, SystemStatus.DEGRADED)

    assert rec.status_changes == [(SystemStatus.HEALTHY, SystemStatus.DEGRADED)]


def test_startup_shutdown() -> None:
    hm = HookManager()
    rec = RecorderPlugin()
    hm.register(rec)

    assert not rec.started
    assert not rec.stopped

    hm.fire_startup()
    assert rec.started

    hm.fire_shutdown()
    assert rec.stopped


def test_builtin_logging_plugin(caplog: logging.LoggerAdapter) -> None:
    hm = HookManager()
    lp = LoggingPlugin()
    hm.register(lp)

    event = _make_event()

    with caplog.at_level(logging.DEBUG, logger="ailm.hooks.builtin"):
        hm.fire_event(event)
        hm.fire_status_change(SystemStatus.HEALTHY, SystemStatus.CRITICAL)
        hm.fire_action_requested("snapshot", {"label": "pre-update"})
        hm.fire_startup()
        hm.fire_shutdown()

    assert "hook:on_event" in caplog.text
    assert "hook:on_status_change" in caplog.text
    assert "hook:on_action_requested" in caplog.text
    assert "hook:on_startup" in caplog.text
    assert "hook:on_shutdown" in caplog.text


def test_action_allowed_with_no_plugins() -> None:
    """No plugins registered => action is allowed (no veto)."""
    hm = HookManager()
    assert hm.fire_action_requested("anything", {}) is True
