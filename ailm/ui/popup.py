"""Frameless popup window showing event feed and system stats."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ailm.config.schema import UIConfig
from ailm.core.models import SystemEvent, SystemStatus
from ailm.ui.feed import FeedWidget
from ailm.ui.widgets import SystemSummaryBar

_DEFAULT_WIDTH = UIConfig().popup_width
_DEFAULT_HEIGHT = UIConfig().popup_height


class FeedPopup(QWidget):
    """Frameless popup window showing event feed and system stats."""

    action_requested = Signal(str, dict)  # action_name, params

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setFixedSize(_DEFAULT_WIDTH, _DEFAULT_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Status bar on top
        self._summary_bar = SystemSummaryBar(self)
        layout.addWidget(self._summary_bar)

        # Feed list below
        self._feed = FeedWidget(self)
        layout.addWidget(self._feed, stretch=1)

    # -- public API -----------------------------------------------------------

    def add_event(self, event: SystemEvent) -> None:
        """Add a new event to the top of the feed."""
        self._feed.add_event(event)

    def load_events(self, events: list[SystemEvent]) -> None:
        """Load historical events into the feed."""
        self._feed.load_events(events)

    def update_status(self, status: SystemStatus) -> None:
        """Update the system summary bar."""
        self._summary_bar.set_status(status)

    def update_stats(self, cpu_pct: float, ram_pct: float, disk_pct: float) -> None:
        """Update the resource usage labels."""
        self._summary_bar.update_stats(cpu_pct, ram_pct, disk_pct)

    @property
    def feed(self) -> FeedWidget:
        """Direct access to the inner FeedWidget."""
        return self._feed

    @property
    def summary_bar(self) -> SystemSummaryBar:
        """Direct access to the inner SystemSummaryBar."""
        return self._summary_bar

    def show_near_tray(self) -> None:
        """Position popup near system tray area.

        On Wayland QSystemTrayIcon.geometry() often returns an invalid rect,
        so we fall back to the bottom-right corner of the primary screen.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            self.show()
            return

        screen_geo = screen.availableGeometry()
        # Bottom-right, with a small margin
        margin = 8
        x = screen_geo.right() - self.width() - margin
        y = screen_geo.bottom() - self.height() - margin
        self.move(x, y)
        self.show()
