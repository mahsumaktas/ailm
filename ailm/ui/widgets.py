"""Shared UI components for ailm."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ailm.core.models import SystemStatus
from ailm.ui.theme import STATUS_COLORS


class _StatusDot(QWidget):
    """Tiny colored circle indicating system status."""

    _DOT_SIZE = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color: QColor = STATUS_COLORS[SystemStatus.HEALTHY]
        self.setFixedSize(self._DOT_SIZE, self._DOT_SIZE)

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 -- Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, self._DOT_SIZE, self._DOT_SIZE)
        painter.end()


class SystemSummaryBar(QWidget):
    """Horizontal bar: system status dot + CPU / RAM / Disk stats."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self._dot = _StatusDot(self)
        layout.addWidget(self._dot)

        self._status_label = QLabel("healthy")
        layout.addWidget(self._status_label)

        layout.addStretch()

        self._cpu_label = QLabel("CPU --")
        self._ram_label = QLabel("RAM --")
        self._disk_label = QLabel("Disk --")
        for lbl in (self._cpu_label, self._ram_label, self._disk_label):
            layout.addWidget(lbl)

    # -- public API -----------------------------------------------------------

    def update_stats(self, cpu_pct: float, ram_pct: float, disk_pct: float) -> None:
        """Refresh the CPU / RAM / Disk percentage labels."""
        self._cpu_label.setText(f"CPU {cpu_pct:.0f}%")
        self._ram_label.setText(f"RAM {ram_pct:.0f}%")
        self._disk_label.setText(f"Disk {disk_pct:.0f}%")

    def set_status(self, status: SystemStatus) -> None:
        """Update the status dot color and label."""
        color = STATUS_COLORS.get(status, STATUS_COLORS[SystemStatus.HEALTHY])
        self._dot.set_color(color)
        self._status_label.setText(status.value)


class ConfirmationDialog(QDialog):
    """'Are you sure?' dialog for safe actions."""

    def __init__(
        self,
        action_name: str,
        params: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm action")
        layout = QVBoxLayout(self)

        msg = QLabel(f"Run <b>{action_name}</b>?")
        layout.addWidget(msg)

        if params:
            detail_parts = [f"  {k}: {v}" for k, v in params.items()]
            detail = QLabel("\n".join(detail_parts))
            detail.setWordWrap(True)
            layout.addWidget(detail)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def confirm(action_name: str, params: dict, parent: QWidget | None = None) -> bool:
        """Show the dialog and return True if the user confirmed."""
        dlg = ConfirmationDialog(action_name, params, parent)
        return dlg.exec() == QDialog.DialogCode.Accepted
