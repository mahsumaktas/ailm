"""Color constants for ailm UI."""

from PySide6.QtGui import QColor

from ailm.core.models import SystemStatus

STATUS_COLORS: dict[SystemStatus, QColor] = {
    SystemStatus.HEALTHY:  QColor("#4CAF50"),   # green
    SystemStatus.DEGRADED: QColor("#FF9800"),   # amber
    SystemStatus.CRITICAL: QColor("#F44336"),   # red
}
