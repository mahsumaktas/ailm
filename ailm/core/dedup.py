"""Event deduplication and rate limiting.

Inspired by pi-power-guard's epsilon + baseline pattern.
Fingerprints messages, suppresses repeats, emits periodic summaries.
"""

import hashlib
import re
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum

# Normalization: strip volatile parts for stable fingerprinting
_CHROMIUM_PREFIX_RE = re.compile(r"\[\d+:\d+:\d+/\d+\.\d+:")  # [66:212:0328/063400.483036:
_PID_RE = re.compile(r"\[\d+\]")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NUM_RE = re.compile(r"\b\d{4,}\b")  # 4+ digit numbers (ports, PIDs, etc.)


class DedupAction(str, Enum):
    EMIT = "emit"
    SUPPRESS = "suppress"
    AGGREGATE = "aggregate"  # source is noisy — emit periodic summary instead


@dataclass
class DedupDecision:
    action: DedupAction
    fingerprint: str
    suppressed_count: int = 0
    aggregate_summary: str | None = None  # set when action=AGGREGATE and it's time to emit


@dataclass
class _FingerprintState:
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_emitted: float | None = None  # None = never emitted


class DedupConfig:
    """Runtime dedup config. Accepts Pydantic model or plain values."""

    __slots__ = (
        "window_seconds", "baseline_seconds", "max_per_source_per_minute",
        "aggregate_threshold", "aggregate_window_seconds",
    )

    def __init__(
        self,
        window_seconds: int = 60,
        baseline_seconds: int = 300,
        max_per_source_per_minute: int = 20,
        aggregate_threshold: int = 5,
        aggregate_window_seconds: int = 60,
    ) -> None:
        self.window_seconds = window_seconds
        self.baseline_seconds = baseline_seconds
        self.max_per_source_per_minute = max_per_source_per_minute
        self.aggregate_threshold = aggregate_threshold
        self.aggregate_window_seconds = aggregate_window_seconds

    @classmethod
    def from_pydantic(cls, cfg) -> "DedupConfig":
        """Create from Pydantic DedupConfig model."""
        return cls(
            window_seconds=cfg.window_seconds,
            baseline_seconds=cfg.baseline_seconds,
            max_per_source_per_minute=cfg.max_per_source_per_minute,
            aggregate_threshold=getattr(cfg, "aggregate_threshold", 5),
            aggregate_window_seconds=getattr(cfg, "aggregate_window_seconds", 60),
        )


def normalize_message(message: str) -> str:
    """Strip volatile parts (PIDs, hex, UUIDs, big numbers, Chrome prefixes)."""
    result = _CHROMIUM_PREFIX_RE.sub("[*:", message)
    result = _PID_RE.sub("[*]", result)
    result = _UUID_RE.sub("<UUID>", result)
    result = _HEX_RE.sub("0x*", result)
    result = _NUM_RE.sub("*", result)
    return result.strip()


def fingerprint(source: str, unit: str, message: str) -> str:
    """Produce a short hash from source + unit + normalized message."""
    normalized = normalize_message(message)
    raw = f"{source}:{unit}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def normalize_summary(summary: str) -> str:
    """Normalize an LLM summary for grouping (lowercase, strip volatile parts)."""
    result = summary.lower()
    result = _PID_RE.sub("", result)
    result = _HEX_RE.sub("", result)
    result = _NUM_RE.sub("", result)
    result = re.sub(r"[^\w\s]", "", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def summary_fingerprint(summary: str) -> str:
    """Produce a hash from a normalized summary for dedup grouping."""
    return hashlib.sha256(normalize_summary(summary).encode()).hexdigest()[:16]


class EventDedup:
    """Stateful dedup filter. Call should_publish() before emitting events."""

    def __init__(self, config: DedupConfig | None = None) -> None:
        self._config = config or DedupConfig()
        self._states: dict[str, _FingerprintState] = {}
        self._rate_windows: dict[str, deque[float]] = {}
        self._last_prune: float = 0.0

    @property
    def config(self) -> DedupConfig:
        return self._config

    @config.setter
    def config(self, value: DedupConfig) -> None:
        self._config = value

    def should_publish(self, fp: str, source: str, message: str = "") -> DedupDecision:
        """Decide whether an event with this fingerprint should be published."""
        now = time.monotonic()
        self._maybe_prune(now)

        # Source-level aggregation disabled in v0.3 — batch LLM handles noise

        state = self._states.get(fp)
        if state is None:
            # First occurrence — still subject to rate limit
            if not self._rate_check(source, now):
                self._states[fp] = _FingerprintState(
                    count=1, first_seen=now, last_seen=now, last_emitted=None,
                )
                return DedupDecision(action=DedupAction.SUPPRESS, fingerprint=fp)
            self._states[fp] = _FingerprintState(
                count=1, first_seen=now, last_seen=now, last_emitted=now,
            )
            self._record_emission(source, now)
            return DedupDecision(action=DedupAction.EMIT, fingerprint=fp)

        state.count += 1
        state.last_seen = now

        # Rate limit check (applies to all emissions)
        if not self._rate_check(source, now):
            return DedupDecision(action=DedupAction.SUPPRESS, fingerprint=fp)

        # Never emitted (was rate-limited on first occurrence) — try now
        if state.last_emitted is None:
            suppressed = state.count - 1
            state.last_emitted = now
            state.count = 1
            self._record_emission(source, now)
            return DedupDecision(
                action=DedupAction.EMIT, fingerprint=fp, suppressed_count=suppressed,
            )

        # Baseline interval: force emit with suppressed count
        if now - state.last_emitted >= self._config.baseline_seconds:
            suppressed = state.count - 1
            state.last_emitted = now
            state.count = 1
            self._record_emission(source, now)
            return DedupDecision(
                action=DedupAction.EMIT, fingerprint=fp, suppressed_count=suppressed,
            )

        # Within window — suppress
        if now - state.last_emitted < self._config.window_seconds:
            return DedupDecision(action=DedupAction.SUPPRESS, fingerprint=fp)

        # Outside window — re-emit
        suppressed = state.count - 1
        state.last_emitted = now
        state.count = 1
        self._record_emission(source, now)
        return DedupDecision(
            action=DedupAction.EMIT, fingerprint=fp, suppressed_count=suppressed,
        )

    def _rate_check(self, source: str, now: float) -> bool:
        """Return True if this source is under the rate limit."""
        window = self._rate_windows.get(source)
        if window is None:
            window = deque()
            self._rate_windows[source] = window

        # Remove entries older than 60s
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()

        return len(window) < self._config.max_per_source_per_minute

    def _record_emission(self, source: str, now: float) -> None:
        """Record an emission timestamp for rate limiting."""
        if source not in self._rate_windows:
            self._rate_windows[source] = deque()
        self._rate_windows[source].append(now)

    def _maybe_prune(self, now: float) -> None:
        """Remove stale fingerprint states to prevent memory growth."""
        # Prune every 2x baseline interval
        interval = self._config.baseline_seconds * 2
        if now - self._last_prune < interval:
            return

        self._last_prune = now
        cutoff = now - interval
        stale = [fp for fp, s in self._states.items() if s.last_seen < cutoff]
        for fp in stale:
            del self._states[fp]

    @property
    def tracked_count(self) -> int:
        """Number of active fingerprints being tracked."""
        return len(self._states)
