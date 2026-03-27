"""Feed widget with scrollable event cards."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ailm.core.models import Severity, SystemEvent, SystemStatus
from ailm.ui.theme import STATUS_COLORS

# Map Severity -> same palette used for SystemStatus
_SEVERITY_COLORS: dict[Severity, QColor] = {
    Severity.INFO: STATUS_COLORS.get(SystemStatus.HEALTHY, QColor("#4CAF50")),
    Severity.WARNING: STATUS_COLORS.get(SystemStatus.DEGRADED, QColor("#FF9800")),
    Severity.CRITICAL: STATUS_COLORS.get(SystemStatus.CRITICAL, QColor("#F44336")),
}

_MAX_FEED_ITEMS = 200


class _SeverityDot(QWidget):
    """Tiny colored dot representing event severity."""

    _SIZE = 10

    def __init__(self, severity: Severity, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = _SEVERITY_COLORS.get(severity, QColor("#4CAF50"))
        self.setFixedSize(self._SIZE, self._SIZE)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, self._SIZE, self._SIZE)
        painter.end()


class EventCard(QFrame):
    """Single event display card: [severity dot] [timestamp] [summary] [source tag]."""

    def __init__(self, event: SystemEvent, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.system_event = event
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        # severity dot
        dot = _SeverityDot(event.severity, self)
        layout.addWidget(dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        # timestamp — compact HH:MM
        ts_text = event.timestamp.strftime("%H:%M")
        ts_label = QLabel(ts_text)
        ts_label.setToolTip(event.timestamp.isoformat())
        layout.addWidget(ts_label)

        # summary (or raw_data fallback)
        summary_text = event.summary or event.raw_data[:120]
        summary_label = QLabel(summary_text)
        summary_label.setWordWrap(True)
        summary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(summary_label, stretch=1)

        # source tag
        src_label = QLabel(event.source)
        src_label.setStyleSheet("color: gray;")
        layout.addWidget(src_label)


class FeedWidget(QScrollArea):
    """Scrollable list of event cards."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._layout.addStretch()  # push cards to top
        self.setWidget(self._container)

        self._cards: list[EventCard] = []

    # -- public API -----------------------------------------------------------

    def add_event(self, event: SystemEvent) -> None:
        """Add a new event card at the top of the feed."""
        card = EventCard(event, self._container)
        # Insert before the stretch item (which is the last item)
        self._layout.insertWidget(0, card)
        self._cards.insert(0, card)
        self._trim()

    def load_events(self, events: list[SystemEvent]) -> None:
        """Replace the current feed with *events* (newest first)."""
        self.clear()
        for ev in events:
            card = EventCard(ev, self._container)
            # Insert before stretch
            insert_pos = self._layout.count() - 1
            self._layout.insertWidget(insert_pos, card)
            self._cards.append(card)
        self._trim()

    def clear(self) -> None:
        """Remove all event cards from the feed."""
        for card in self._cards:
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    @property
    def card_count(self) -> int:
        return len(self._cards)

    # -- internals ------------------------------------------------------------

    def _trim(self) -> None:
        """Keep the feed within _MAX_FEED_ITEMS by removing oldest cards."""
        while len(self._cards) > _MAX_FEED_ITEMS:
            old = self._cards.pop()
            self._layout.removeWidget(old)
            old.deleteLater()
