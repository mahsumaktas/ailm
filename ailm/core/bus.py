"""Asyncio-based event bus with typed pub/sub."""

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

from ailm.core.models import EventType, SystemEvent

logger = logging.getLogger(__name__)

type SyncCallback = Callable[[SystemEvent], None]
type AsyncCallback = Callable[[SystemEvent], Coroutine[Any, Any, None]]
type Callback = SyncCallback | AsyncCallback


class EventBus:
    """Central nervous system — all components communicate through this."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: asyncio.Queue[SystemEvent | None] = asyncio.Queue(maxsize=maxsize)
        self._subscribers: dict[EventType | None, list[Callback]] = defaultdict(list)
        self._task: asyncio.Task[None] | None = None

    def subscribe(self, event_type: EventType | None, callback: Callback) -> None:
        """Register a callback. Pass None for event_type to receive all events."""
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType | None, callback: Callback) -> None:
        try:
            self._subscribers[event_type].remove(callback)
        except ValueError:
            pass

    async def publish(self, event: SystemEvent) -> None:
        """Publish an event. Drops silently if queue is full."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event bus full, dropping: %s/%s", event.type.value, event.source)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._dispatch())

    async def stop(self) -> None:
        if self._task is None:
            return
        try:
            self._queue.put_nowait(None)  # sentinel — non-blocking to avoid deadlock
        except asyncio.QueueFull:
            self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass  # expected when queue was full and we cancelled
        self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def _dispatch(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                break

            # Snapshot lists to prevent mutation during iteration
            for cb in list(self._subscribers.get(event.type, [])):
                await self._invoke(cb, event)

            for cb in list(self._subscribers.get(None, [])):
                await self._invoke(cb, event)

    async def _invoke(self, callback: Callback, event: SystemEvent) -> None:
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception:
            logger.exception("Subscriber error for %s", event.type.value)
