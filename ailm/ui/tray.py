"""System tray icon for ailm.

Displays a colored circle that reflects the overall system health:
  green  — HEALTHY
  amber  — DEGRADED (e.g. Ollama offline, unresolved warnings)
  red    — CRITICAL (disk >95 %, unrecovered service failures)

Right-click menu provides quick actions (Show Feed, Quit).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ailm.core.models import SystemStatus
from ailm.ui.theme import STATUS_COLORS

_ICON_SIZE = 64


class AilmTray(QSystemTrayIcon):
    """System tray icon with status-driven color."""

    show_feed_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = SystemStatus.HEALTHY
        self._update_icon()
        self._build_menu()
        self.setToolTip("ailm — system companion")

    # -- Public API -----------------------------------------------------------

    @property
    def status(self) -> SystemStatus:
        return self._status

    def set_status(self, status: SystemStatus) -> None:
        """Change the displayed status and repaint the icon."""
        if status == self._status:
            return
        self._status = status
        self._update_icon()
        self.setToolTip(f"ailm — {status.value}")

    # -- Internals ------------------------------------------------------------

    def _update_icon(self) -> None:
        color = STATUS_COLORS.get(self._status, QColor("#4CAF50"))
        pixmap = QPixmap(_ICON_SIZE, _ICON_SIZE)
        pixmap.fill(QColor(0, 0, 0, 0))  # transparent background

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(color.darker(120))
        margin = 4
        painter.drawEllipse(margin, margin, _ICON_SIZE - 2 * margin, _ICON_SIZE - 2 * margin)
        painter.end()

        self.setIcon(QIcon(pixmap))

    def _build_menu(self) -> None:
        menu = QMenu()

        show_action = menu.addAction("Show Feed")
        show_action.triggered.connect(self.show_feed_requested.emit)

        menu.addSeparator()

        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_requested.emit)

        self.setContextMenu(menu)
