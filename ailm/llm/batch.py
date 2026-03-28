"""Batch LLM analysis — replaces per-event classification.

Instead of calling LLM for every event (252 calls/min, GPU 95%),
runs a single batch analysis every 5 minutes on unanalyzed events.
GPU usage: ~30s every 5min = <10% duty cycle.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.db.repository import EventRepository
from ailm.llm.client import OllamaClient

logger = logging.getLogger(__name__)

BATCH_SYSTEM = """\
You are a Linux system analyst for an Arch/CachyOS desktop with \
NVIDIA GPU, Docker, Tailscale, and Ollama.

Analyze a batch of system events. For each event, provide a short \
summary and action. Also identify patterns across events.

Rules:
- severity "critical" = crash, OOM, GPU hang, data loss risk
- severity "warning" = degraded, retryable errors, pressure
- action "ignore" = known harmless
- action "investigate" = unclear root cause
- action "restart_service" = service crashed, restart likely fixes
- action "reboot" = kernel-level issue"""

BATCH_USER = """\
<events>
{events}
</events>

Analyze these {count} events. Respond with ONLY JSON:
{{"events": [{{"id": N, "summary": "short", "action": "ignore|investigate|restart_service|reboot"}}], \
"patterns": ["pattern1"], "overall": "1-2 sentence system status"}}"""


class BatchAnalyzer:
    """Periodic batch LLM analysis of unanalyzed events."""

    def __init__(
        self,
        repo: EventRepository,
        llm: OllamaClient | None,
        bus=None,
    ) -> None:
        self._repo = repo
        self._llm = llm
        self._bus = bus

    async def analyze_batch(self) -> None:
        """Called every 5 minutes by scheduler."""
        if self._llm is None or not self._llm.available:
            return

        since = datetime.now(timezone.utc) - timedelta(minutes=5)
        unanalyzed = await self._repo.get_unanalyzed_since(since, limit=50)
        if not unanalyzed:
            return

        # Build batch prompt
        event_lines = []
        for e in unanalyzed:
            event_lines.append(f"[id={e.id}] [{e.severity.value}] {e.source}: {e.raw_data[:200]}")

        prompt = BATCH_USER.replace("{events}", "\n".join(event_lines))
        prompt = prompt.replace("{count}", str(len(unanalyzed)))

        result = await self._llm.generate(prompt, system=BATCH_SYSTEM)
        if result is None:
            # LLM unavailable — set simple summaries from raw_data
            for e in unanalyzed:
                await self._fallback_summary(e)
            return

        # Parse batch response
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            logger.debug("Batch analysis returned invalid JSON, using raw summaries")
            for e in unanalyzed:
                await self._fallback_summary(e)
            return

        # Apply per-event summaries
        event_map = {e.id: e for e in unanalyzed}
        for item in parsed.get("events", []):
            eid = item.get("id")
            if eid not in event_map:
                continue
            event = event_map[eid]
            summary = item.get("summary", event.raw_data[:120])
            event.summary = summary
            await self._repo.update_summary(eid, summary)

            action = item.get("action", "").lower()
            if action in ("restart_service", "reboot", "investigate"):
                event.user_action = action
                await self._repo.update_user_action(eid, action)

        # Remaining unanalyzed (not in LLM response) get raw summaries
        analyzed_ids = {item.get("id") for item in parsed.get("events", [])}
        for e in unanalyzed:
            if e.id not in analyzed_ids:
                await self._fallback_summary(e)

        # Publish patterns as ANALYSIS event
        patterns = parsed.get("patterns", [])
        overall = parsed.get("overall", "")
        if patterns or overall:
            summary_text = overall
            if patterns:
                summary_text += " Patterns: " + "; ".join(patterns[:3])
            if self._bus is not None:
                await self._bus.publish(SystemEvent(
                    type=EventType.BRIEFING,
                    severity=Severity.INFO,
                    raw_data=json.dumps({"patterns": patterns, "overall": overall}),
                    source="batch_analysis",
                    summary=summary_text[:200],
                ))

        logger.info("Batch analyzed %d events", len(unanalyzed))

    async def _fallback_summary(self, event: SystemEvent) -> None:
        """Set a simple summary from raw_data when LLM is unavailable."""
        raw = event.raw_data
        summary = raw[raw.index("msg=") + 4:][:120] if "msg=" in raw else raw[:120]
        await self._repo.update_summary(event.id, summary)
