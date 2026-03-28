"""Source protocol, polling base, and watchdog base."""

import asyncio
import logging
from contextlib import suppress
from queue import Empty, SimpleQueue
from typing import Protocol, runtime_checkable

from watchdog.observers import Observer

from ailm.core.bus import EventBus

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.2


async def cancel_task(task: asyncio.Task | None) -> None:
    """Cancel an asyncio task and suppress CancelledError."""
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@runtime_checkable
class Source(Protocol):
    """Protocol implemented by every event source."""

    name: str

    async def start(self, bus: EventBus) -> None:
        """Attach the source to an event bus and begin monitoring."""
        ...

    async def stop(self) -> None:
        """Stop the source and release any background resources."""
        ...


class PollingSource:
    """Base class for interval-based event sources."""

    name: str = ""

    def __init__(self, interval: int) -> None:
        self._interval = interval
        self._bus: EventBus | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def bus(self) -> EventBus:
        """Return the bound event bus or raise if the source is not started."""
        if self._bus is None:
            raise RuntimeError(f"Source '{self.name}' not started — call start() first")
        return self._bus

    async def start(self, bus: EventBus) -> None:
        """Bind the event bus and start the polling task."""
        self._bus = bus
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the polling task if it is running."""
        await cancel_task(self._task)
        self._task = None

    async def check(self) -> None:
        """Perform a single polling cycle."""
        raise NotImplementedError

    async def _loop(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                logger.exception("%s check failed", self.name)
            await asyncio.sleep(self._interval)


class WatchdogSource:
    """Base class for watchdog-based event sources with debounce and async safety."""

    name: str = ""

    def __init__(self) -> None:
        self._bus: EventBus | None = None
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bridge_task: asyncio.Task[None] | None = None
        self._bridge_queue: SimpleQueue[tuple[str, object]] = SimpleQueue()
        self._wake_event: asyncio.Event | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._lock: asyncio.Lock | None = None

    @property
    def bus(self) -> EventBus:
        """Return the bound event bus or raise if the source is not started."""
        if self._bus is None:
            raise RuntimeError(f"Source '{self.name}' not started — call start() first")
        return self._bus

    def _setup_observer(self) -> Observer:
        raise NotImplementedError

    async def start(self, bus: EventBus) -> None:
        """Bind the event bus, start the queue bridge, and launch the observer."""
        self._bus = bus
        self._loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._bridge_task = asyncio.create_task(self._bridge_loop())
        self._observer = self._setup_observer()
        self._observer.daemon = True
        self._observer.start()

    async def stop(self) -> None:
        """Stop timers, stop the observer, and tear down the bridge task."""
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
            self._debounce_handle = None
        if self._observer is not None:
            self._observer.stop()
            await asyncio.to_thread(self._observer.join, 5)
            self._observer = None
        await cancel_task(self._bridge_task)
        self._bridge_task = None
        self._loop = None

    def _schedule_debounced(self, coro_factory) -> None:
        """Debounced bridge from watchdog thread to asyncio. Collapses rapid events."""
        if self._loop is None:
            return
        self._bridge_queue.put_nowait(("debounce", coro_factory))
        self._loop.call_soon_threadsafe(self._wake_event.set)

    def _debounce_on_loop(self, coro_factory) -> None:
        """Runs on event loop thread — safe to touch _debounce_handle."""
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()

        def _fire():
            self._debounce_handle = None
            if self._loop is None:
                return
            task = self._loop.create_task(coro_factory())
            task.add_done_callback(self._log_task_error)

        self._debounce_handle = self._loop.call_later(DEBOUNCE_SECONDS, _fire)

    def _schedule_async(self, coro_factory) -> None:
        """Immediate (non-debounced) bridge from watchdog thread to asyncio."""
        if self._loop is not None:
            self._bridge_queue.put_nowait(("async", coro_factory))
            self._loop.call_soon_threadsafe(self._wake_event.set)

    async def _bridge_loop(self) -> None:
        """Wait for wake signal, then drain queued callbacks from watchdog threads."""
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()
            self._drain_bridge_queue()

    def _drain_bridge_queue(self) -> None:
        """Drain queued callbacks from watchdog threads."""
        while True:
            try:
                mode, coro_factory = self._bridge_queue.get_nowait()
            except Empty:
                break

            if mode == "debounce":
                self._debounce_on_loop(coro_factory)
            else:
                if self._loop is None:
                    continue
                task = self._loop.create_task(coro_factory())
                task.add_done_callback(self._log_task_error)

    @staticmethod
    def _log_task_error(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Watchdog async callback error: %s", exc, exc_info=exc)
