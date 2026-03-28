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
from ailm.sources.docker import DockerSource
from ailm.sources.network import ServicePortSource, TailscaleSource
from ailm.sources.nvidia import NvidiaSource
from ailm.sources.orphan import OrphanSource
from ailm.sources.security import SecuritySource
from ailm.sources.smart import SmartSource
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
        self.sources.append(DockerSource())
        self.sources.append(NvidiaSource(interval=30, trend_tracker=self.trend_tracker))
        self.sources.append(SmartSource())
        self.sources.append(TailscaleSource())
        self.sources.append(ServicePortSource())
        self.sources.append(SecuritySource())
        self.sources.append(OrphanSource())

        if cfg.journald_enabled:
            dedup_cfg = self.config.dedup
            dedup = EventDedup(CoreDedupConfig.from_pydantic(dedup_cfg))
            self.sources.append(JournaldSource(
                dedup=dedup,
                noise_patterns=cfg.noise_patterns,
            ))

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

        # Metric thresholds: metric_name -> slope_threshold (units/hour)
        _TREND_THRESHOLDS = {
            "disk_usage_pct": self.config.sources.disk_slope_threshold,  # %/hr
            "cpu_pct": 20.0,          # sustained CPU climb >20%/hr
            "ram_pct": 10.0,          # memory leak >10%/hr
            "swap_pct": 5.0,          # swap growth >5%/hr
            "net_recv_mbps": 50.0,    # unusual incoming traffic
            "net_sent_mbps": 50.0,    # unusual outgoing traffic
        }

        # Track previous network counters for delta
        _prev_net = {"recv": 0, "sent": 0, "time": 0.0}

        # Periodic health + status prune + queue drain + trend sampling (every 30s)
        async def health_job() -> None:
            self.status_tracker.prune()

            # System metrics → TrendTracker (30s intervals)
            try:
                import time as _time
                import psutil

                now_mono = _time.monotonic()

                # Disk
                disk_pct = psutil.disk_usage("/").percent

                # CPU (non-blocking, 0-interval returns since last call)
                cpu_pct = psutil.cpu_percent(interval=0)

                # Memory
                mem = psutil.virtual_memory()
                ram_pct = mem.percent
                swap = psutil.swap_memory()
                swap_pct = swap.percent

                # Network (delta since last call → Mbps)
                net = psutil.net_io_counters()
                dt = now_mono - _prev_net["time"] if _prev_net["time"] > 0 else 30.0
                if dt > 0 and _prev_net["time"] > 0:
                    recv_mbps = (net.bytes_recv - _prev_net["recv"]) * 8 / dt / 1_000_000
                    sent_mbps = (net.bytes_sent - _prev_net["sent"]) * 8 / dt / 1_000_000
                else:
                    recv_mbps = 0.0
                    sent_mbps = 0.0
                _prev_net["recv"] = net.bytes_recv
                _prev_net["sent"] = net.bytes_sent
                _prev_net["time"] = now_mono

                # Feed all metrics to trend tracker
                metrics = {
                    "disk_usage_pct": disk_pct,
                    "cpu_pct": cpu_pct,
                    "ram_pct": ram_pct,
                    "swap_pct": swap_pct,
                    "net_recv_mbps": recv_mbps,
                    "net_sent_mbps": sent_mbps,
                }
                for name, value in metrics.items():
                    threshold = _TREND_THRESHOLDS.get(name)
                    if threshold is None:
                        continue
                    alert = self.trend_tracker.update(name, value, slope_threshold=threshold)
                    if alert is not None:
                        summary = alert.summary
                        # Disk time-to-full projection
                        if name == "disk_usage_pct" and alert.slope > 0:
                            remaining = 100.0 - alert.current_value
                            hours_to_full = remaining / alert.slope
                            if hours_to_full < 72:
                                days = hours_to_full / 24
                                summary += f" — projected full in {days:.1f} days"
                        await self.bus.publish(SystemEvent(
                            type=EventType.TREND_ALERT,
                            severity=Severity.WARNING,
                            raw_data=f"metric={alert.metric} slope={alert.slope:.3f} ema={alert.ema:.1f} current={alert.current_value:.1f}",
                            source="trend",
                            summary=summary,
                        ))

                # Event frequency tracking
                if self.repo is not None:
                    from datetime import datetime, timedelta, timezone
                    one_hr_ago = datetime.now(timezone.utc) - timedelta(hours=1)
                    counts = await self.repo.get_event_count_by_type(one_hr_ago)
                    total_per_hr = sum(counts.values())
                    self.trend_tracker.update(
                        "events_per_hour", float(total_per_hr), slope_threshold=100.0,
                    )
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

    _VALID_ACTIONS = frozenset({"restart_service", "reboot", "investigate"})

    async def _do_classify(self, event: SystemEvent) -> None:
        """Background classification task — runs after bus dispatch completes."""
        try:
            if self.llm is not None and self.llm.available:
                result = await self.llm.classify_log(event.raw_data)
                if result is not None:
                    await self._apply_classification(event, result)
                    return

            # LLM unavailable — queue for later
            from ailm.llm.prompts import CLASSIFICATION_SYSTEM, build_classification_prompt

            async def on_classified(result_str: str) -> None:
                try:
                    import json
                    parsed = json.loads(result_str)
                except (json.JSONDecodeError, AttributeError):
                    parsed = {"summary": result_str[:120]}
                await self._apply_classification(event, parsed)

            self.llm_queue.enqueue(LLMTask(
                prompt=build_classification_prompt(event.raw_data),
                system=CLASSIFICATION_SYSTEM,
                callback=on_classified,
            ))
        except Exception:
            logger.debug("Classification failed for event %s", event.id)

    async def _apply_classification(self, event: SystemEvent, result: dict) -> None:
        """Apply LLM classification result to event and persist changes."""
        from ailm.core.dedup import summary_fingerprint
        from ailm.core.models import Severity, severity_max

        # Summary + root cause (append if present)
        summary = result.get("summary", event.raw_data[:120])
        root_cause = result.get("root_cause", "")
        if root_cause and len(summary) + len(root_cause) < 200:
            summary = f"{summary} — {root_cause}"
        event.summary = summary
        event.summary_hash = summary_fingerprint(summary)

        # Severity upgrade (never downgrade)
        llm_sev = result.get("severity", "").lower()
        if llm_sev in ("info", "warning", "critical"):
            upgraded = severity_max(event.severity, Severity(llm_sev))
            if upgraded != event.severity:
                event.severity = upgraded

        # Action (with noise override)
        action = result.get("action", "").lower()
        if action == "ignore":
            event.user_action = None  # don't store "ignore" as action
        elif action in self._VALID_ACTIONS:
            event.user_action = action

        # Persist to DB
        if event.id is not None and self.repo is not None:
            await self.repo.update_summary(event.id, summary, event.summary_hash)
            if event.user_action:
                await self.repo.update_user_action(event.id, event.user_action)
