"""Application orchestrator — wires all ailm subsystems together."""

import logging
from pathlib import Path

from ailm.config.schema import AilmConfig
from ailm.core.bus import EventBus
from ailm.core.crash import CrashDetector
from ailm.core.dedup import DedupConfig as CoreDedupConfig, EventDedup  # alias: schema also has DedupConfig
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.ringlog import RingBufferLog
from ailm.core.trend import TrendTracker
from ailm.core.status import StatusTracker
from ailm.db.connection import Database
from ailm.db.repository import EventRepository
from ailm.hooks import HookManager
from ailm.hooks.builtin import LoggingPlugin
from ailm.llm import LLMTask, LLMTaskQueue, OllamaClient
from ailm.scheduler import SchedulerEngine, generate_morning_briefing
from ailm.sources.base import Source
from ailm.sources.disk import DiskMonitor
from ailm.sources.journald import JournaldSource
from ailm.sources.pacman import PacmanSource
from ailm.sources.pacnew import PacnewSource
from ailm.sources.reboot import RebootSource
from ailm.sources.services import ServiceMonitor
from ailm.sources.snapshot import SnapshotSource

logger = logging.getLogger(__name__)


class Application:
    """Top-level orchestrator that owns and manages every subsystem."""

    def __init__(self, config: AilmConfig) -> None:
        self.config = config
        self.bus = EventBus()
        self.db: Database | None = None
        self.repo: EventRepository | None = None
        self.llm: OllamaClient | None = None
        self.llm_queue = LLMTaskQueue()
        self.hooks = HookManager()
        self.status_tracker = StatusTracker()
        self.ringlog: RingBufferLog | None = None
        self._crash_detector: CrashDetector | None = None
        self.scheduler: SchedulerEngine | None = None
        self.trend_tracker = TrendTracker(
            alpha=config.trend.alpha,
            window_size=config.trend.fast_window_size,
            cooldown_seconds=config.trend.cooldown_seconds,
        )
        self.sources: list[Source] = []
        self._started = False

    async def start(self) -> None:
        """Boot all subsystems in dependency order."""
        # 1. Connect DB
        self.db = Database(self.config.db.path)
        await self.db.connect()
        self.repo = EventRepository(self.db)
        logger.info("Database connected")

        # 2. Start EventBus
        await self.bus.start()
        logger.info("EventBus started")

        # 3. Start LLM client (if enabled)
        if self.config.llm.enabled:
            self.llm = OllamaClient(
                base_url=self.config.llm.base_url,
                model=self.config.llm.model,
                timeout=self.config.llm.timeout,
            )
            await self.llm.start()
            self.status_tracker.set_llm_available(self.llm.available)
            if self.llm.available:
                logger.info("LLM client connected")
            else:
                logger.warning("LLM client started but Ollama not reachable")

        # 4. Ring buffer log (crash-resilient)
        data_dir = Path(self.config.db.path).parent
        if self.config.ringlog.enabled:
            self.ringlog = RingBufferLog(
                log_dir=data_dir / "ringlog",
                max_lines=self.config.ringlog.max_lines,
                max_archives=self.config.ringlog.max_archives,
                sync_interval=self.config.ringlog.sync_interval,
            )
            self.ringlog.open()

        # 5. Boot crash detection (after ring log, before sources)
        self._crash_detector = CrashDetector(data_dir, self.ringlog)
        crash_report = self._crash_detector.on_start()
        if crash_report is not None:
            await self.bus.publish(SystemEvent(
                type=EventType.BOOT_ANALYSIS,
                severity=Severity.WARNING,
                raw_data=f"prev_state={crash_report.previous_state} lines={len(crash_report.pre_crash_log)}",
                source="ailm",
                summary=crash_report.analysis,
            ))

        # 6. Wire bus subscribers BEFORE sources start (so no startup events are lost)
        self.bus.subscribe(None, self._persist_event)       # → DB
        self.bus.subscribe(None, self.status_tracker.on_event)  # → StatusTracker
        self.bus.subscribe(None, self._fire_hook_event)     # → Hooks
        if self.ringlog is not None:
            self.bus.subscribe(None, self._ringlog_event)  # → Ring log
        self.bus.subscribe(EventType.LOG_ANOMALY, self._classify_log_event)  # → LLM

        # 5. Register and start event sources
        self._register_sources()
        for source in self.sources:
            await source.start(self.bus)
            logger.info("Source started: %s", source.name)

        # 6. Setup scheduler
        self.scheduler = SchedulerEngine()
        await self.scheduler.start()
        await self._setup_schedules()
        logger.info("Scheduler started")

        # 8. Wire status tracker → hooks
        self.status_tracker.on_status_change(self.hooks.fire_status_change)

        # 9. Register built-in hooks
        self.hooks.register(LoggingPlugin())

        # 10. Fire startup hooks
        self.hooks.fire_startup()
        self._started = True
        logger.info("Application started — all systems wired")

    async def stop(self) -> None:
        """Shut down all subsystems in reverse order."""
        if not self._started:
            return

        # 1. Fire shutdown hooks
        self.hooks.fire_shutdown()

        # 2. Stop scheduler
        if self.scheduler is not None:
            await self.scheduler.stop()
            self.scheduler = None

        # 3. Stop sources
        for source in reversed(self.sources):
            await source.stop()
        self.sources.clear()

        # 4. Stop LLM client
        if self.llm is not None:
            await self.llm.close()
            self.llm = None

        # 5. Stop EventBus
        await self.bus.stop()

        # 6. Mark clean shutdown
        if self._crash_detector is not None:
            self._crash_detector.on_stop()
            self._crash_detector = None

        # 7. Close ring log
        if self.ringlog is not None:
            self.ringlog.close()
            self.ringlog = None

        # 7. Close DB
        if self.db is not None:
            await self.db.close()
            self.db = None
            self.repo = None

        self._started = False
        logger.info("Application stopped")

    def _register_sources(self) -> None:
        """Conditionally register sources based on config."""
        cfg = self.config.sources

        self.sources.append(
            DiskMonitor(
                cfg.disk_warn_pct, cfg.disk_critical_pct, cfg.disk_interval,
                trend_tracker=self.trend_tracker,
                slope_threshold=cfg.disk_slope_threshold,
            )
        )
        self.sources.append(ServiceMonitor(interval=cfg.service_interval))
        self.sources.append(PacmanSource(cfg.pacman_log_path))
        self.sources.append(RebootSource())

        if Path(cfg.snapshot_path).is_dir():
            self.sources.append(SnapshotSource(cfg.snapshot_path))

        self.sources.append(PacnewSource())

        if cfg.journald_enabled:
            dedup_cfg = self.config.dedup
            dedup = EventDedup(CoreDedupConfig.from_pydantic(dedup_cfg))
            self.sources.append(JournaldSource(dedup=dedup))

    async def _setup_schedules(self) -> None:
        """Configure scheduled jobs."""
        if self.scheduler is None or self.db is None:
            return

        llm = self.llm  # may be None — briefing uses fallback

        async def briefing_job() -> None:
            await generate_morning_briefing(self.db, llm, self.bus)

        await self.scheduler.add_cron_job(
            briefing_job, self.config.scheduler.briefing_cron, job_id="morning_briefing",
        )

        retention = self.config.db.retention_days

        async def cleanup_job() -> None:
            if self.repo is not None:
                deleted = await self.repo.cleanup_old_events(retention)
                if deleted:
                    logger.info("Cleaned up %d old events", deleted)

        await self.scheduler.add_cron_job(cleanup_job, "0 3 * * *", job_id="db_cleanup")

        # Periodic health + status prune + queue drain + trend sampling (every 30s)
        async def health_job() -> None:
            self.status_tracker.prune()

            # Fast-poll disk trend (30s intervals → full window in 10min with window_size=20)
            try:
                import psutil
                pct = psutil.disk_usage("/").percent
                alert = self.trend_tracker.update(
                    "disk_usage_pct", pct,
                    slope_threshold=self.config.sources.disk_slope_threshold,
                )
                if alert is not None:
                    await self.bus.publish(SystemEvent(
                        type=EventType.TREND_ALERT,
                        severity=Severity.WARNING,
                        raw_data=f"metric={alert.metric} slope={alert.slope:.3f} ema={alert.ema:.1f}",
                        source="trend",
                        summary=alert.summary,
                    ))
            except Exception:
                pass  # psutil failure should not break health check

            if self.llm is not None:
                was_available = self.llm.available
                await self.llm.health_check()
                self.status_tracker.set_llm_available(self.llm.available)
                if self.llm.available and not was_available:
                    drained = await self.llm_queue.drain(self.llm)
                    if drained:
                        logger.info("LLM back online, drained %d queued tasks", drained)

        await self.scheduler.add_interval_job(health_job, 30, job_id="health_check")

    async def reload_config(self) -> None:
        """Reload config from disk and apply changes to running subsystems."""
        from ailm.config import load_config

        try:
            new_config = load_config()
        except Exception:
            logger.exception("Config reload failed — keeping current config")
            return

        changes: list[str] = []
        old = self.config

        # LLM model/timeout change
        if (new_config.llm.model != old.llm.model
                or new_config.llm.timeout != old.llm.timeout
                or new_config.llm.base_url != old.llm.base_url):
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
                    logger.exception("Failed to start new LLM client, restoring old")
                    try:
                        self.llm = OllamaClient(
                            base_url=old.llm.base_url,
                            model=old.llm.model,
                            timeout=old.llm.timeout,
                        )
                        await self.llm.start()
                    except Exception:
                        logger.exception("Rollback also failed, LLM disabled until next reload")
                        self.llm = None

        # Source intervals
        for source in self.sources:
            if source.name == "disk" and hasattr(source, "_interval"):
                if new_config.sources.disk_interval != old.sources.disk_interval:
                    source._interval = new_config.sources.disk_interval
                    changes.append(f"disk_interval: {old.sources.disk_interval}s -> {new_config.sources.disk_interval}s")

        # Dedup config
        if (new_config.dedup.window_seconds != old.dedup.window_seconds
                or new_config.dedup.baseline_seconds != old.dedup.baseline_seconds
                or new_config.dedup.max_per_source_per_minute != old.dedup.max_per_source_per_minute):
            for source in self.sources:
                if hasattr(source, "_dedup") and source._dedup is not None:
                    source._dedup.config = CoreDedupConfig.from_pydantic(new_config.dedup)
                    changes.append("dedup config updated")
                    break

        self.config = new_config

        if changes:
            logger.info("Config reloaded: %s", "; ".join(changes))
        else:
            logger.info("Config reloaded: no changes detected")

    async def maybe_insert_welcome(self) -> None:
        """Insert a welcome BRIEFING event on first run (empty DB)."""
        if self.repo is None:
            return
        existing = await self.repo.get_recent_events(limit=1)
        if existing:
            return
        welcome = SystemEvent(
            type=EventType.BRIEFING, severity=Severity.INFO,
            raw_data="first_run", source="ailm",
            summary="Welcome to ailm! I watch your system — packages, services, "
                    "disk usage, logs — and surface what matters. Daily briefing at 06:00.",
        )
        await self.repo.insert_event(welcome)
        logger.info("First run — welcome briefing inserted")

    async def _persist_event(self, event: SystemEvent) -> None:
        """Bus subscriber: persist every event to DB."""
        if self.repo is None:
            return
        try:
            await self.repo.insert_event(event)
        except Exception:
            logger.exception("Failed to persist event: %s/%s", event.type.value, event.source)

    def _fire_hook_event(self, event: SystemEvent) -> None:
        """Bus subscriber: forward events to hook system."""
        self.hooks.fire_event(event)

    def _ringlog_event(self, event: SystemEvent) -> None:
        """Bus subscriber: write events to crash-resilient ring log."""
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

    async def _classify_log_event(self, event: SystemEvent) -> None:
        """Bus subscriber (LOG_ANOMALY only): fire-and-forget classification.

        Spawns a background task so bus dispatch is not blocked by LLM latency.
        """
        if event.summary is not None:
            return
        if self.llm is None:
            return
        import asyncio
        asyncio.create_task(self._do_classify(event))

    async def _do_classify(self, event: SystemEvent) -> None:
        """Background classification task — runs after bus dispatch completes."""
        try:
            if self.llm is not None and self.llm.available:
                result = await self.llm.classify_log(event.raw_data)
                if result is not None:
                    summary = result.get("summary", event.raw_data[:120])
                    event.summary = summary
                    # event.id is set by _persist_event (runs before this task completes)
                    if event.id is not None and self.repo is not None:
                        await self.repo.update_summary(event.id, summary)
                    return

            # LLM unavailable — queue for later
            from ailm.llm.prompts import CLASSIFICATION_SYSTEM, build_classification_prompt

            async def on_classified(result_str: str) -> None:
                try:
                    import json
                    parsed = json.loads(result_str)
                    summary = parsed.get("summary", result_str[:120])
                except (json.JSONDecodeError, AttributeError):
                    summary = result_str[:120]
                if event.id is not None and self.repo is not None:
                    await self.repo.update_summary(event.id, summary)

            self.llm_queue.enqueue(LLMTask(
                prompt=build_classification_prompt(event.raw_data),
                system=CLASSIFICATION_SYSTEM,
                callback=on_classified,
            ))
        except Exception:
            logger.debug("Classification failed for event %s", event.id)
