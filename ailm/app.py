"""Application orchestrator — wires all ailm subsystems together.

v0.3 architecture: 3 collectors + batch LLM instead of 20 sources + per-event LLM.
"""

import logging
from pathlib import Path

from ailm.config.schema import AilmConfig
from ailm.core.bus import EventBus
from ailm.core.crash import CrashDetector
from ailm.core.dedup import DedupConfig as CoreDedupConfig, EventDedup
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.ringlog import RingBufferLog
from ailm.core.status import StatusTracker
from ailm.core.trend import TrendTracker
from ailm.db.connection import Database
from ailm.db.repository import EventRepository
from ailm.hooks import HookManager
from ailm.hooks.builtin import LoggingPlugin
from ailm.llm import OllamaClient
from ailm.llm.batch import BatchAnalyzer
from ailm.scheduler import SchedulerEngine, generate_morning_briefing
from ailm.sources.base import Source
from ailm.sources.external import ExternalCollector
from ailm.sources.journald import JournaldSource
from ailm.sources.metrics import MetricsCollector
from ailm.sources.pacman import PacmanSource
from ailm.sources.reboot import RebootSource
from ailm.sources.services import ServiceMonitor
from ailm.sources.snapshot import SnapshotSource

logger = logging.getLogger(__name__)


class Application:
    """Top-level orchestrator — 3 collectors + batch LLM."""

    def __init__(self, config: AilmConfig) -> None:
        self.config = config
        self.bus = EventBus()
        self.db: Database | None = None
        self.repo: EventRepository | None = None
        self.llm: OllamaClient | None = None
        self.batch_analyzer: BatchAnalyzer | None = None
        self.hooks = HookManager()
        self.status_tracker = StatusTracker()
        self.scheduler: SchedulerEngine | None = None
        self.trend_tracker = TrendTracker(
            alpha=config.trend.alpha,
            window_size=config.trend.fast_window_size,
            cooldown_seconds=config.trend.cooldown_seconds,
        )
        self.ringlog: RingBufferLog | None = None
        self._crash_detector: CrashDetector | None = None
        self.sources: list[Source] = []
        self._started = False

    async def start(self) -> None:
        """Boot all subsystems in dependency order."""
        # 1. DB
        self.db = Database(self.config.db.path)
        await self.db.connect()
        self.repo = EventRepository(self.db)
        logger.info("Database connected")

        # 2. EventBus
        await self.bus.start()
        logger.info("EventBus started")

        # 3. LLM
        if self.config.llm.enabled:
            self.llm = OllamaClient(
                base_url=self.config.llm.base_url,
                model=self.config.llm.model,
                timeout=self.config.llm.timeout,
            )
            await self.llm.start()
            self.status_tracker.set_llm_available(self.llm.available)
            logger.info("LLM %s", "connected" if self.llm.available else "unavailable")

        # 4. Ring buffer log
        data_dir = Path(self.config.db.path).parent
        if self.config.ringlog.enabled:
            self.ringlog = RingBufferLog(
                log_dir=data_dir / "ringlog",
                max_lines=self.config.ringlog.max_lines,
                max_archives=self.config.ringlog.max_archives,
                sync_interval=self.config.ringlog.sync_interval,
            )
            self.ringlog.open()

        # 5. Crash detection
        self._crash_detector = CrashDetector(data_dir, self.ringlog)
        crash = self._crash_detector.on_start()
        if crash is not None:
            await self.bus.publish(SystemEvent(
                type=EventType.BOOT_ANALYSIS, severity=Severity.WARNING,
                raw_data=f"prev_state={crash.previous_state}",
                source="ailm", summary=crash.analysis,
            ))

        # 6. Bus subscribers
        self.bus.subscribe(None, self._persist_event)
        self.bus.subscribe(None, self.status_tracker.on_event)
        self.bus.subscribe(None, self._fire_hook_event)
        if self.ringlog is not None:
            self.bus.subscribe(None, self._ringlog_event)

        # 7. Sources (3 collectors + 4 kept sources)
        self._register_sources()
        for source in self.sources:
            await source.start(self.bus)
            logger.info("Source started: %s", source.name)

        # 8. Scheduler
        self.scheduler = SchedulerEngine()
        await self.scheduler.start()
        await self._setup_schedules()
        logger.info("Scheduler started")

        # 9. Hooks
        self.status_tracker.on_status_change(self.hooks.fire_status_change)
        self.hooks.register(LoggingPlugin())
        self.hooks.fire_startup()
        self._started = True
        logger.info("Application started — all systems wired")

    async def stop(self) -> None:
        """Shut down all subsystems in reverse order."""
        if not self._started:
            return
        self.hooks.fire_shutdown()
        if self.scheduler is not None:
            await self.scheduler.stop()
            self.scheduler = None
        for source in reversed(self.sources):
            await source.stop()
        self.sources.clear()
        if self.llm is not None:
            await self.llm.close()
            self.llm = None
        await self.bus.stop()
        if self._crash_detector is not None:
            self._crash_detector.on_stop()
            self._crash_detector = None
        if self.ringlog is not None:
            self.ringlog.close()
            self.ringlog = None
        if self.db is not None:
            await self.db.close()
            self.db = None
            self.repo = None
        self._started = False
        logger.info("Application stopped")

    def _register_sources(self) -> None:
        """Register 3 collectors + 4 kept sources."""
        cfg = self.config.sources

        # Collector 1: All system metrics (replaces 7+ sources + health_job)
        self.sources.append(MetricsCollector(
            interval=30,
            trend=self.trend_tracker,
            disk_warn=cfg.disk_warn_pct,
            disk_crit=cfg.disk_critical_pct,
        ))

        # Collector 2: External services (replaces 6+ sources)
        self.sources.append(ExternalCollector(interval=60))

        # Kept sources (already efficient)
        self.sources.append(ServiceMonitor(interval=cfg.service_interval))
        self.sources.append(PacmanSource(cfg.pacman_log_path))
        self.sources.append(RebootSource())

        if Path(cfg.snapshot_path).is_dir():
            self.sources.append(SnapshotSource(cfg.snapshot_path))

        if cfg.journald_enabled:
            dedup = EventDedup(CoreDedupConfig.from_pydantic(self.config.dedup))
            self.sources.append(JournaldSource(dedup=dedup))

    async def _setup_schedules(self) -> None:
        """Configure scheduled jobs — simple and focused."""
        if self.scheduler is None or self.db is None:
            return

        llm = self.llm

        # Morning briefing (06:00)
        async def briefing_job() -> None:
            await generate_morning_briefing(self.db, llm, self.bus)

        await self.scheduler.add_cron_job(
            briefing_job, self.config.scheduler.briefing_cron, job_id="morning_briefing",
        )

        # DB cleanup (03:00)
        retention = self.config.db.retention_days

        async def cleanup_job() -> None:
            if self.repo is not None:
                deleted = await self.repo.cleanup_old_events(retention)
                if deleted:
                    logger.info("Cleaned up %d old events", deleted)

        await self.scheduler.add_cron_job(cleanup_job, "0 3 * * *", job_id="db_cleanup")

        # Health check (every 30s) — just LLM health + status prune
        async def health_job() -> None:
            self.status_tracker.prune()
            if self.llm is not None:
                was = self.llm.available
                await self.llm.health_check()
                self.status_tracker.set_llm_available(self.llm.available)

        await self.scheduler.add_interval_job(health_job, 30, job_id="health_check")

        # Batch LLM analysis (every 5 minutes)
        if self.repo is not None:
            self.batch_analyzer = BatchAnalyzer(self.repo, self.llm, self.bus)

            async def batch_job() -> None:
                if self.batch_analyzer is not None:
                    await self.batch_analyzer.analyze_batch()

            await self.scheduler.add_interval_job(batch_job, 300, job_id="batch_analysis")

    async def reload_config(self) -> None:
        """Reload config from disk and apply changes."""
        from ailm.config import load_config

        try:
            new_config = load_config()
        except Exception:
            logger.exception("Config reload failed")
            return

        changes: list[str] = []
        old = self.config

        if (new_config.llm.model != old.llm.model
                or new_config.llm.timeout != old.llm.timeout):
            if self.llm is not None:
                await self.llm.close()
                self.llm = None
            if new_config.llm.enabled:
                self.llm = OllamaClient(
                    base_url=new_config.llm.base_url,
                    model=new_config.llm.model,
                    timeout=new_config.llm.timeout,
                )
                try:
                    await self.llm.start()
                    changes.append(f"LLM: {old.llm.model} -> {new_config.llm.model}")
                except Exception:
                    logger.exception("New LLM failed, disabling")
                    self.llm = None

        self.config = new_config
        if changes:
            logger.info("Config reloaded: %s", "; ".join(changes))
        else:
            logger.info("Config reloaded: no changes")

    async def maybe_insert_welcome(self) -> None:
        """Insert a welcome event on first run."""
        if self.repo is None:
            return
        existing = await self.repo.get_recent_events(limit=1)
        if existing:
            return
        await self.repo.insert_event(SystemEvent(
            type=EventType.BRIEFING, severity=Severity.INFO,
            raw_data="first_run", source="ailm",
            summary="Welcome to ailm! Monitoring your system 24/7.",
        ))

    async def _persist_event(self, event: SystemEvent) -> None:
        if self.repo is None:
            return
        try:
            await self.repo.insert_event(event)
        except Exception:
            logger.exception("Failed to persist event")

    def _fire_hook_event(self, event: SystemEvent) -> None:
        self.hooks.fire_event(event)

    def _ringlog_event(self, event: SystemEvent) -> None:
        if self.ringlog is None:
            return
        self.ringlog.write(
            event.timestamp,
            event.severity.value.upper(),
            event.source,
            f"type={event.type.value} summary={event.summary or event.raw_data[:200]}",
        )
        if event.severity == Severity.CRITICAL:
            self.ringlog.sync_now()
