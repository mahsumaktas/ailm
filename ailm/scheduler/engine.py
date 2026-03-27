"""Simple asyncio-based scheduler — replaces APScheduler v4 alpha.

APScheduler v4 alpha requires module-level callables (no closures).
This engine uses plain asyncio tasks with cron matching, which supports
closures and is simpler to maintain.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

type AsyncJobFunc = Callable[..., Coroutine[Any, Any, None]]


def _parse_cron(cron_expr: str) -> dict[str, str]:
    """Parse 5-field cron into a dict. Format: minute hour day month day_of_week."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron, got {len(parts)}: {cron_expr!r}")
    return dict(zip(("minute", "hour", "day", "month", "day_of_week"), parts))


def _cron_matches(fields: dict[str, str], dt: datetime) -> bool:
    """Check if datetime matches cron fields."""
    checks = {
        "minute": dt.minute,
        "hour": dt.hour,
        "day": dt.day,
        "month": dt.month,
        "day_of_week": dt.weekday(),  # 0=Monday
    }
    for key, value in checks.items():
        pattern = fields[key]
        if pattern == "*":
            continue
        # Simple: exact match or comma-separated values
        allowed = {int(v) for v in pattern.split(",")}
        if value not in allowed:
            return False
    return True


class _Job:
    def __init__(self, job_id: str, func: AsyncJobFunc,
                 cron: dict[str, str] | None = None, interval: int | None = None):
        self.job_id = job_id
        self.func = func
        self.cron = cron
        self.interval = interval


class SchedulerEngine:
    """Lightweight asyncio scheduler supporting cron and interval jobs."""

    def __init__(self) -> None:
        self._jobs: list[_Job] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("Scheduler started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._jobs.clear()
        logger.info("Scheduler stopped")

    async def add_cron_job(self, func: AsyncJobFunc, cron_expr: str, job_id: str) -> None:
        fields = _parse_cron(cron_expr)
        job = _Job(job_id=job_id, func=func, cron=fields)
        self._jobs.append(job)
        task = asyncio.create_task(self._run_cron(job))
        self._tasks.append(task)
        logger.info("Cron job added: %s (%s)", job_id, cron_expr)

    async def add_interval_job(self, func: AsyncJobFunc, seconds: int, job_id: str) -> None:
        job = _Job(job_id=job_id, func=func, interval=seconds)
        self._jobs.append(job)
        task = asyncio.create_task(self._run_interval(job))
        self._tasks.append(task)
        logger.info("Interval job added: %s (every %ds)", job_id, seconds)

    async def _run_cron(self, job: _Job) -> None:
        """Check cron match every 60s, fire when matched."""
        last_fired_minute = -1
        while True:
            await asyncio.sleep(30)
            now = datetime.now().astimezone()  # local time — cron is user-facing
            if now.minute == last_fired_minute:
                continue
            if job.cron and _cron_matches(job.cron, now):
                last_fired_minute = now.minute
                try:
                    await job.func()
                except Exception:
                    logger.exception("Cron job %s failed", job.job_id)

    async def _run_interval(self, job: _Job) -> None:
        """Run job at fixed interval."""
        while True:
            await asyncio.sleep(job.interval or 60)
            try:
                await job.func()
            except Exception:
                logger.exception("Interval job %s failed", job.job_id)
