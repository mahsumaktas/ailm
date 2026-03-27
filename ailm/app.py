"""Application orchestrator — wires all ailm subsystems together."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ailm.config.schema import AilmConfig
from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent, SystemStatus
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
        self.scheduler: SchedulerEngine | None = None
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

        # 4. Wire bus subscribers BEFORE sources start (so no startup events are lost)
        self.bus.subscribe(None, self._persist_event)       # → DB
        self.bus.subscribe(None, self.status_tracker.on_event)  # → StatusTracker
        self.bus.subscribe(None, self._fire_hook_event)     # → Hooks
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

        # 6. Close DB
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
            DiskMonitor(cfg.disk_warn_pct, cfg.disk_critical_pct, cfg.disk_interval)
        )
        self.sources.append(ServiceMonitor(interval=cfg.service_interval))
        self.sources.append(PacmanSource(cfg.pacman_log_path))
        self.sources.append(RebootSource())

        if Path(cfg.snapshot_path).is_dir():
            self.sources.append(SnapshotSource(cfg.snapshot_path))

        if cfg.journald_enabled:
            self.sources.append(JournaldSource())

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

        # Periodic health + status prune + queue drain (every 30s)
        async def health_job() -> None:
            # Prune stale events from status tracker (prevents stuck red/orange)
            self.status_tracker.prune()

            if self.llm is not None:
                was_available = self.llm.available
                await self.llm.health_check()
                self.status_tracker.set_llm_available(self.llm.available)
                if self.llm.available and not was_available:
                    drained = await self.llm_queue.drain(self.llm)
                    if drained:
                        logger.info("LLM back online, drained %d queued tasks", drained)

        await self.scheduler.add_interval_job(health_job, 30, job_id="health_check")

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

    async def _classify_log_event(self, event: SystemEvent) -> None:
        """Bus subscriber (LOG_ANOMALY only): classify via LLM or queue."""
        if event.summary is not None:
            return  # already classified

        if self.llm is not None and self.llm.available:
            result = await self.llm.classify_log(event.raw_data)
            if result is not None:
                event.summary = result.get("summary", event.raw_data[:120])
                # Update in DB if already persisted
                if event.id is not None and self.repo is not None:
                    await self.repo.update_user_action(event.id, None)  # triggers re-read
                return

        # LLM unavailable — queue for later with callback to update DB
        if self.llm is not None:
            from ailm.llm.prompts import CLASSIFICATION_SYSTEM, build_classification_prompt

            event_id = event.id

            async def on_classified(result: str) -> None:
                """Called when queued classification completes."""
                try:
                    import json
                    parsed = json.loads(result)
                    summary = parsed.get("summary", result[:120])
                except (json.JSONDecodeError, AttributeError):
                    summary = result[:120]
                if event_id is not None and self.repo is not None:
                    event.summary = summary
                    logger.debug("Backfilled classification for event %d", event_id)

            self.llm_queue.enqueue(LLMTask(
                prompt=build_classification_prompt(event.raw_data),
                system=CLASSIFICATION_SYSTEM,
                callback=on_classified,
            ))
