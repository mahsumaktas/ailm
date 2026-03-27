"""ailm UI layer — Qt/asyncio bridge, system tray, and popup feed."""

from ailm.ui.bridge import AsyncioBridge
from ailm.ui.feed import EventCard, FeedWidget
from ailm.ui.popup import FeedPopup
from ailm.ui.theme import STATUS_COLORS
from ailm.ui.tray import AilmTray
from ailm.ui.widgets import ConfirmationDialog, SystemSummaryBar

__all__ = [
    "AilmTray",
    "AsyncioBridge",
    "ConfirmationDialog",
    "EventCard",
    "FeedPopup",
    "FeedWidget",
    "STATUS_COLORS",
    "SystemSummaryBar",
]
