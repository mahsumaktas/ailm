"""EMA trend detection with half-window slope analysis.

Inspired by pi-power-guard's VoltageTracker.
Detects gradual metric changes (disk filling, latency creeping)
before hard thresholds are breached.
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Literal


@dataclass
class TrendAlert:
    """Emitted when a metric's trend crosses the slope threshold."""

    metric: str
    slope: float              # units per hour
    direction: Literal["rising", "falling"]
    current_value: float
    ema: float

    @property
    def summary(self) -> str:
        arrow = "rising" if self.direction == "rising" else "falling"
        return f"{self.metric} {arrow} at {abs(self.slope):.2f}/hour (current: {self.current_value:.1f})"


class _MetricState:
    __slots__ = ("ema", "window", "last_alert_time")

    def __init__(self, initial_value: float, window_size: int) -> None:
        self.ema: float = initial_value
        self.window: deque[tuple[float, float]] = deque(maxlen=window_size)
        self.last_alert_time: float | None = None


class TrendTracker:
    """Track multiple named metrics with EMA + slope detection."""

    def __init__(
        self,
        alpha: float = 0.1,
        window_size: int = 60,
        cooldown_seconds: int = 600,
    ) -> None:
        if not 0 < alpha < 1:
            raise ValueError("alpha must be between 0 and 1 exclusive")
        self._alpha = alpha
        self._window_size = window_size
        self._cooldown = cooldown_seconds
        self._metrics: dict[str, _MetricState] = {}
        # Per-metric slope thresholds (set via configure_threshold)
        self._thresholds: dict[str, float] = {}

    def configure_threshold(self, metric: str, slope_threshold: float) -> None:
        """Set the slope threshold for a specific metric (units per hour)."""
        self._thresholds[metric] = slope_threshold

    def update(
        self,
        metric: str,
        value: float,
        slope_threshold: float | None = None,
    ) -> TrendAlert | None:
        """Feed a new sample. Returns TrendAlert if slope exceeds threshold."""
        now = time.monotonic()

        state = self._metrics.get(metric)
        if state is None:
            state = _MetricState(value, self._window_size)
            self._metrics[metric] = state

        # Update EMA
        state.ema = self._alpha * value + (1 - self._alpha) * state.ema
        state.window.append((now, state.ema))

        # Need at least half-window to compute slope
        min_samples = max(self._window_size // 2, 4)
        if len(state.window) < min_samples * 2:
            return None

        # Half-window slope
        half = len(state.window) // 2
        first_half = list(state.window)[:half]
        second_half = list(state.window)[half:]

        first_mean = sum(v for _, v in first_half) / len(first_half)
        second_mean = sum(v for _, v in second_half) / len(second_half)

        time_delta_hours = (second_half[-1][0] - first_half[0][0]) / 3600.0
        if time_delta_hours < 0.001:  # avoid division by zero
            return None

        slope = (second_mean - first_mean) / time_delta_hours

        # Determine threshold
        threshold = slope_threshold
        if threshold is None:
            threshold = self._thresholds.get(metric)
        if threshold is None:
            return None  # no threshold configured

        # Check if slope exceeds threshold
        if abs(slope) < threshold:
            return None

        # Cooldown check
        if state.last_alert_time is not None and now - state.last_alert_time < self._cooldown:
            return None

        state.last_alert_time = now
        direction: Literal["rising", "falling"] = "rising" if slope > 0 else "falling"
        return TrendAlert(
            metric=metric,
            slope=slope,
            direction=direction,
            current_value=value,
            ema=state.ema,
        )

    def get_ema(self, metric: str) -> float | None:
        """Return current EMA for a metric, or None if not tracked."""
        state = self._metrics.get(metric)
        return state.ema if state is not None else None
