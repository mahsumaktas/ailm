"""APScheduler v4 wrapper with asyncio fallback.

Uses APScheduler's AsyncScheduler for cron/interval scheduling.
The wrapper isolates APScheduler's alpha API so swapping backends
requires changes in this file only.
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from apscheduler import AsyncScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

type AsyncJobFunc = Callable[..., Coroutine[Any, Any, None]]


def _parse_cron(cron_expr: str) -> CronTrigger:
    """Parse a 5-field cron expression into an APScheduler CronTrigger.

    Format: minute hour day month day_of_week
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        msg = f"Expected 5-field cron expression, got {len(parts)}: {cron_expr!r}"
        raise ValueError(msg)

    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )


class SchedulerEngine:
    """Thin wrapper around APScheduler v4 AsyncScheduler.

    Provides a stable interface so the rest of ailm doesn't depend
    on APScheduler's alpha API directly.

    APScheduler v4 alpha requires context manager initialization
    before calling start_in_background/add_schedule. This wrapper
    handles that lifecycle transparently.
    """

    def __init__(self) -> None:
        self._scheduler: AsyncScheduler | None = None
        self._running = False
        self._schedule_ids: list[str] = []

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Initialize services and start the scheduler in background mode."""
        if self._running:
            return
        self._scheduler = AsyncScheduler()
        # APScheduler v4 requires __aenter__ to initialize internal services
        await self._scheduler.__aenter__()
        await self._scheduler.start_in_background()
        self._running = True
        logger.info("Scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler and clean up resources."""
        if not self._running:
            return
        self._running = False
        self._schedule_ids.clear()
        if self._scheduler is not None:
            await self._scheduler.__aexit__(None, None, None)
            self._scheduler = None
        logger.info("Scheduler stopped")

    def _ensure_started(self) -> AsyncScheduler:
        if self._scheduler is None or not self._running:
            raise RuntimeError("Scheduler not started — call start() first")
        return self._scheduler

    async def add_cron_job(self, func: AsyncJobFunc, cron_expr: str, job_id: str) -> None:
        """Schedule a coroutine function using a 5-field cron expression.

        Must be called after start().
        """
        scheduler = self._ensure_started()
        trigger = _parse_cron(cron_expr)
        schedule_id = await scheduler.add_schedule(func, trigger, id=job_id)
        self._schedule_ids.append(schedule_id)
        logger.info("Cron job added: %s (%s)", job_id, cron_expr)

    async def add_interval_job(self, func: AsyncJobFunc, seconds: int, job_id: str) -> None:
        """Schedule a coroutine function at a fixed interval.

        Must be called after start().
        """
        scheduler = self._ensure_started()
        trigger = IntervalTrigger(seconds=seconds)
        schedule_id = await scheduler.add_schedule(func, trigger, id=job_id)
        self._schedule_ids.append(schedule_id)
        logger.info("Interval job added: %s (every %ds)", job_id, seconds)
