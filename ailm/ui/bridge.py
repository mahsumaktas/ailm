"""Asyncio <-> Qt bridge.

Runs the asyncio event loop in a dedicated QThread so that coroutines
(EventBus subscriptions, LLM calls, DB queries) execute without blocking
the Qt GUI thread.  The two worlds communicate through Qt Signals which
are thread-safe by design.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future

from PySide6.QtCore import QThread, Signal


class AsyncioBridge(QThread):
    """Background thread hosting an asyncio event loop."""

    event_received = Signal(object)   # SystemEvent -> UI
    status_changed = Signal(str)      # SystemStatus value -> tray icon

    def __init__(self, parent=None) -> None:
        """Initialize the bridge thread."""
        super().__init__(parent)
        self.loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        """Create a fresh event loop and run it until stop_loop() is called."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()
            self.loop = None

    def submit(self, coro) -> Future | None:
        """Schedule *coro* on the asyncio loop from the Qt thread.

        Returns a ``concurrent.futures.Future`` that can be used to retrieve
        the coroutine's result, or ``None`` if the loop is not running.
        """
        if self.loop is not None and self.loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self.loop)
        return None

    def stop_loop(self) -> None:
        """Ask the asyncio loop to stop (non-blocking, thread-safe)."""
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
