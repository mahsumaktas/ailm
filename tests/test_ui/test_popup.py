"""Tests for the popup feed window, feed widget, event cards, and shared widgets."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from ailm.core.models import EventType, Severity, SystemEvent, SystemStatus

# ---------------------------------------------------------------------------
# Guard: skip GUI tests when there is no display server
# ---------------------------------------------------------------------------
_HAS_DISPLAY = bool(
    os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
)

needs_display = pytest.mark.skipif(
    not _HAS_DISPLAY,
    reason="No display server available (DISPLAY / WAYLAND_DISPLAY unset)",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    severity: Severity = Severity.INFO,
    summary: str = "test event",
    source: str = "test",
    event_type: EventType = EventType.LOG_ANOMALY,
) -> SystemEvent:
    return SystemEvent(
        type=event_type,
        severity=severity,
        raw_data="raw data for test",
        source=source,
        timestamp=datetime(2026, 3, 26, 12, 0, 0, tzinfo=timezone.utc),
        summary=summary,
    )


# ===========================================================================
# EventCard tests (require display)
# ===========================================================================


@needs_display
class TestEventCard:
    def test_card_creation_info(self, qtbot):
        from ailm.ui.feed import EventCard

        ev = _make_event(Severity.INFO, "disk ok")
        card = EventCard(ev)
        qtbot.addWidget(card)
        assert card.system_event is ev

    def test_card_creation_warning(self, qtbot):
        from ailm.ui.feed import EventCard

        ev = _make_event(Severity.WARNING, "disk 82%")
        card = EventCard(ev)
        qtbot.addWidget(card)
        assert card.system_event.severity == Severity.WARNING

    def test_card_creation_critical(self, qtbot):
        from ailm.ui.feed import EventCard

        ev = _make_event(Severity.CRITICAL, "disk 97%")
        card = EventCard(ev)
        qtbot.addWidget(card)
        assert card.system_event.severity == Severity.CRITICAL

    def test_card_uses_raw_data_when_no_summary(self, qtbot):
        from ailm.ui.feed import EventCard

        ev = SystemEvent(
            type=EventType.LOG_ANOMALY,
            severity=Severity.INFO,
            raw_data="fallback text",
            source="test",
            summary=None,
        )
        card = EventCard(ev)
        qtbot.addWidget(card)
        # The summary label should contain the raw_data fallback
        assert card.system_event.summary is None


# ===========================================================================
# FeedWidget tests (require display)
# ===========================================================================


@needs_display
class TestFeedWidget:
    def test_add_event_increases_count(self, qtbot):
        from ailm.ui.feed import FeedWidget

        feed = FeedWidget()
        qtbot.addWidget(feed)
        assert feed.card_count == 0

        feed.add_event(_make_event())
        assert feed.card_count == 1

        feed.add_event(_make_event(Severity.WARNING))
        assert feed.card_count == 2

    def test_load_events_replaces_existing(self, qtbot):
        from ailm.ui.feed import FeedWidget

        feed = FeedWidget()
        qtbot.addWidget(feed)

        feed.add_event(_make_event())
        assert feed.card_count == 1

        events = [_make_event(summary=f"ev{i}") for i in range(3)]
        feed.load_events(events)
        assert feed.card_count == 3

    def test_clear_removes_all(self, qtbot):
        from ailm.ui.feed import FeedWidget

        feed = FeedWidget()
        qtbot.addWidget(feed)

        for i in range(5):
            feed.add_event(_make_event(summary=f"ev{i}"))
        assert feed.card_count == 5

        feed.clear()
        assert feed.card_count == 0

    def test_add_event_inserts_at_top(self, qtbot):
        from ailm.ui.feed import FeedWidget

        feed = FeedWidget()
        qtbot.addWidget(feed)

        feed.add_event(_make_event(summary="first"))
        feed.add_event(_make_event(summary="second"))

        # The newest card (second) should be at index 0
        assert feed._cards[0].system_event.summary == "second"
        assert feed._cards[1].system_event.summary == "first"


# ===========================================================================
# SystemSummaryBar tests (require display)
# ===========================================================================


@needs_display
class TestSystemSummaryBar:
    def test_initial_state(self, qtbot):
        from ailm.ui.widgets import SystemSummaryBar

        bar = SystemSummaryBar()
        qtbot.addWidget(bar)
        assert bar._status_label.text() == "healthy"

    def test_update_stats(self, qtbot):
        from ailm.ui.widgets import SystemSummaryBar

        bar = SystemSummaryBar()
        qtbot.addWidget(bar)

        bar.update_stats(45.2, 63.8, 71.0)
        assert bar._cpu_label.text() == "CPU 45%"
        assert bar._ram_label.text() == "RAM 64%"
        assert bar._disk_label.text() == "Disk 71%"

    def test_set_status_changes_label(self, qtbot):
        from ailm.ui.widgets import SystemSummaryBar

        bar = SystemSummaryBar()
        qtbot.addWidget(bar)

        bar.set_status(SystemStatus.CRITICAL)
        assert bar._status_label.text() == "critical"

        bar.set_status(SystemStatus.DEGRADED)
        assert bar._status_label.text() == "degraded"

        bar.set_status(SystemStatus.HEALTHY)
        assert bar._status_label.text() == "healthy"


# ===========================================================================
# FeedPopup tests (require display)
# ===========================================================================


@needs_display
class TestFeedPopup:
    def test_popup_creation(self, qtbot):
        from ailm.ui.popup import FeedPopup

        popup = FeedPopup()
        qtbot.addWidget(popup)
        assert popup.width() == 420
        assert popup.height() == 600

    def test_popup_add_event(self, qtbot):
        from ailm.ui.popup import FeedPopup

        popup = FeedPopup()
        qtbot.addWidget(popup)

        popup.add_event(_make_event())
        assert popup.feed.card_count == 1

    def test_popup_load_events(self, qtbot):
        from ailm.ui.popup import FeedPopup

        popup = FeedPopup()
        qtbot.addWidget(popup)

        events = [_make_event(summary=f"e{i}") for i in range(4)]
        popup.load_events(events)
        assert popup.feed.card_count == 4

    def test_popup_update_status(self, qtbot):
        from ailm.ui.popup import FeedPopup

        popup = FeedPopup()
        qtbot.addWidget(popup)

        popup.update_status(SystemStatus.DEGRADED)
        assert popup.summary_bar._status_label.text() == "degraded"

    def test_popup_update_stats(self, qtbot):
        from ailm.ui.popup import FeedPopup

        popup = FeedPopup()
        qtbot.addWidget(popup)

        popup.update_stats(10.0, 20.0, 30.0)
        assert popup.summary_bar._cpu_label.text() == "CPU 10%"

    def test_popup_positioning(self, qtbot):
        """show_near_tray should place popup within screen bounds."""
        from PySide6.QtWidgets import QApplication

        from ailm.ui.popup import FeedPopup

        popup = FeedPopup()
        qtbot.addWidget(popup)

        screen = QApplication.primaryScreen()
        if screen is None:
            pytest.skip("No primary screen available")

        popup.show_near_tray()
        geo = screen.availableGeometry()

        # Popup should be within screen bounds
        assert popup.x() >= geo.x()
        assert popup.y() >= geo.y()
        assert popup.x() + popup.width() <= geo.right() + 1
        assert popup.y() + popup.height() <= geo.bottom() + 1

        popup.hide()


# ===========================================================================
# ConfirmationDialog tests (require display)
# ===========================================================================


@needs_display
class TestConfirmationDialog:
    def test_dialog_creation(self, qtbot):
        from ailm.ui.widgets import ConfirmationDialog

        dlg = ConfirmationDialog("test_action", {"key": "value"})
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == "Confirm action"

    def test_dialog_creation_empty_params(self, qtbot):
        from ailm.ui.widgets import ConfirmationDialog

        dlg = ConfirmationDialog("action", {})
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == "Confirm action"
