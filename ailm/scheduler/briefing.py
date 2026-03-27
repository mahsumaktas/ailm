"""Morning briefing job — summarizes last 24h of system events.

Runs via the scheduler at the configured cron time (default 06:00).
Falls back to a plain-text summary when LLM is unavailable.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.db.connection import Database
from ailm.db.repository import EventRepository
from ailm.llm.client import OllamaClient

logger = logging.getLogger(__name__)

# Keep the event summary under ~300 tokens (~1200 chars).
# Each event line is roughly 80 chars, so cap at 15 lines.
MAX_SUMMARY_LINES = 15
MAX_SUMMARY_CHARS = 1200


def _build_events_summary(events: list[SystemEvent]) -> str:
    """Compress events into a prompt-friendly summary string.

    Groups by type, includes severity and count, stays within
    the ~300 token budget.
    """
    if not events:
        return "No events in the last 24 hours."

    lines: list[str] = []
    type_counts: Counter[str] = Counter()

    for event in events:
        type_counts[event.type.value] += 1

    # Header with counts
    lines.append(f"Total events: {len(events)}")
    for etype, count in type_counts.most_common():
        lines.append(f"  {etype}: {count}")

    # Add individual notable events (critical/warning first)
    notable = sorted(
        events,
        key=lambda e: (0 if e.severity == Severity.CRITICAL else
                       1 if e.severity == Severity.WARNING else 2),
    )

    lines.append("")
    lines.append("Notable events:")
    for event in notable:
        desc = event.summary or event.raw_data[:80]
        line = f"- [{event.severity.value.upper()}] {event.type.value}: {desc}"
        lines.append(line)
        if len(lines) >= MAX_SUMMARY_LINES:
            remaining = len(notable) - MAX_SUMMARY_LINES + 3  # account for header lines
            if remaining > 0:
                lines.append(f"  ... and {remaining} more events")
            break

    summary = "\n".join(lines)
    # Hard truncation to stay within budget
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS - 3] + "..."
    return summary


def _build_fallback_briefing(events: list[SystemEvent]) -> str:
    """Plain-text briefing when LLM is unavailable."""
    if not events:
        return "Morning briefing: No events in the last 24 hours. System quiet."

    type_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()

    for event in events:
        type_counts[event.type.value] += 1
        severity_counts[event.severity.value] += 1

    lines = [f"Morning briefing (LLM unavailable — plain summary):"]
    lines.append(f"Total events in last 24h: {len(events)}")
    lines.append("")

    # Severity breakdown
    lines.append("By severity:")
    for sev in ("critical", "warning", "info"):
        count = severity_counts.get(sev, 0)
        if count > 0:
            lines.append(f"  {sev}: {count}")

    # Type breakdown
    lines.append("")
    lines.append("By type:")
    for etype, count in type_counts.most_common():
        lines.append(f"  {etype}: {count}")

    # Highlight critical events
    critical = [e for e in events if e.severity == Severity.CRITICAL]
    if critical:
        lines.append("")
        lines.append("Critical events requiring attention:")
        for event in critical[:5]:
            desc = event.summary or event.raw_data[:80]
            lines.append(f"  - {event.type.value}: {desc}")

    return "\n".join(lines)


async def generate_morning_briefing(
    db: Database,
    llm: OllamaClient,
    bus: EventBus,
) -> None:
    """Generate and publish the morning briefing.

    1. Query events from last 24h
    2. Summarize into a prompt string (~300 token budget)
    3. If LLM available: generate briefing via LLM
    4. If LLM unavailable: create fallback plain text briefing
    5. Store as BRIEFING event in DB
    6. Publish to bus
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    repo = EventRepository(db)

    try:
        events = await repo.get_events_since(since)
    except Exception:
        logger.exception("Failed to query events for briefing")
        return

    # Build summary for LLM prompt
    events_summary = _build_events_summary(events)

    # Try LLM first, fall back to plain text
    briefing_text: str | None = None
    if llm is not None and llm.available:
        try:
            briefing_text = await llm.generate_briefing(events_summary)
        except Exception:
            logger.warning("LLM briefing generation failed, using fallback")

    if briefing_text is None:
        briefing_text = _build_fallback_briefing(events)

    # Store and publish
    briefing_event = SystemEvent(
        type=EventType.BRIEFING,
        severity=Severity.INFO,
        raw_data=events_summary,
        source="scheduler",
        summary=briefing_text,
    )

    try:
        await repo.insert_event(briefing_event)
    except Exception:
        logger.exception("Failed to store briefing event")

    try:
        await bus.publish(briefing_event)
    except Exception:
        logger.exception("Failed to publish briefing event")

    logger.info("Morning briefing generated (%d events summarized)", len(events))
