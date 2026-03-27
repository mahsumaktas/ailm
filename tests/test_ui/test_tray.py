"""Tests for the ailm UI layer: theme, bridge, and tray icon."""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from PySide6.QtGui import QColor

from ailm.core.models import SystemStatus
from ailm.ui.theme import STATUS_COLORS

# ---------------------------------------------------------------------------
# Guard: skip GUI tests when there is no display server
# ---------------------------------------------------------------------------
_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

needs_display = pytest.mark.skipif(
    not _HAS_DISPLAY,
    reason="No display server available (DISPLAY / WAYLAND_DISPLAY unset)",
)


# ===========================================================================
# Theme tests (no display required)
# ===========================================================================

class TestTheme:
    def test_status_colors_has_all_statuses(self):
        for status in SystemStatus:
            assert status in STATUS_COLORS, f"Missing color for {status}"

    def test_colors_are_qcolor(self):
        for status, color in STATUS_COLORS.items():
            assert isinstance(color, QColor), f"{status} color is not QColor"

    def test_specific_hex_values(self):
        assert STATUS_COLORS[SystemStatus.HEALTHY].name()  == "#4caf50"
        assert STATUS_COLORS[SystemStatus.DEGRADED].name() == "#ff9800"
        assert STATUS_COLORS[SystemStatus.CRITICAL].name() == "#f44336"


# ===========================================================================
# Bridge tests (no display required — QThread works without QApplication)
# ===========================================================================

class TestBridge:
    def test_bridge_starts_and_stops(self):
        from ailm.ui.bridge import AsyncioBridge

        bridge = AsyncioBridge()
        bridge.start()
        # Give the thread time to spin up the loop
        deadline = time.monotonic() + 3
        while bridge.loop is None and time.monotonic() < deadline:
            time.sleep(0.05)

        assert bridge.loop is not None, "Event loop was not created"
        assert bridge.loop.is_running()

        bridge.stop_loop()
        bridge.wait(3000)
        assert not bridge.isRunning()

    def test_submit_runs_coroutine(self):
        from ailm.ui.bridge import AsyncioBridge

        bridge = AsyncioBridge()
        bridge.start()
        deadline = time.monotonic() + 3
        while bridge.loop is None and time.monotonic() < deadline:
            time.sleep(0.05)

        async def add(a: int, b: int) -> int:
            return a + b

        future = bridge.submit(add(2, 3))
        assert future is not None
        result = future.result(timeout=3)
        assert result == 5

        bridge.stop_loop()
        bridge.wait(3000)

    def test_submit_returns_none_when_stopped(self):
        from ailm.ui.bridge import AsyncioBridge

        bridge = AsyncioBridge()
        # Not started — loop is None
        coro = asyncio.sleep(0)
        result = bridge.submit(coro)
        assert result is None
        # Explicitly close the never-awaited coroutine to suppress warning
        coro.close()


# ===========================================================================
# Tray tests (require a display — use pytest-qt's qtbot)
# ===========================================================================

@needs_display
class TestTray:
    def test_tray_creation(self, qtbot):
        from ailm.ui.tray import AilmTray

        tray = AilmTray()
        assert tray.status == SystemStatus.HEALTHY
        assert not tray.icon().isNull()

    def test_status_change_updates_icon(self, qtbot):
        from ailm.ui.tray import AilmTray

        tray = AilmTray()
        old_key = tray.icon().cacheKey()

        tray.set_status(SystemStatus.CRITICAL)
        assert tray.status == SystemStatus.CRITICAL
        # Icon cache key must differ after repaint
        assert tray.icon().cacheKey() != old_key

    def test_status_same_value_no_repaint(self, qtbot):
        from ailm.ui.tray import AilmTray

        tray = AilmTray()
        old_key = tray.icon().cacheKey()
        tray.set_status(SystemStatus.HEALTHY)  # same as initial
        assert tray.icon().cacheKey() == old_key

    def test_menu_actions(self, qtbot):
        from ailm.ui.tray import AilmTray

        tray = AilmTray()
        menu = tray.contextMenu()
        assert menu is not None

        action_texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "Show Feed" in action_texts
        assert "Quit" in action_texts

    def test_show_feed_signal(self, qtbot):
        from ailm.ui.tray import AilmTray

        tray = AilmTray()
        with qtbot.waitSignal(tray.show_feed_requested, timeout=1000):
            menu = tray.contextMenu()
            for action in menu.actions():
                if action.text() == "Show Feed":
                    action.trigger()
                    break

    def test_quit_signal(self, qtbot):
        from ailm.ui.tray import AilmTray

        tray = AilmTray()
        with qtbot.waitSignal(tray.quit_requested, timeout=1000):
            menu = tray.contextMenu()
            for action in menu.actions():
                if action.text() == "Quit":
                    action.trigger()
                    break

    def test_tooltip_updates_on_status_change(self, qtbot):
        from ailm.ui.tray import AilmTray

        tray = AilmTray()
        assert "companion" in tray.toolTip()

        tray.set_status(SystemStatus.DEGRADED)
        assert "degraded" in tray.toolTip()
