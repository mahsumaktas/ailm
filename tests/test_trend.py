"""Tests for TrendTracker — EMA, slope detection, cooldown."""

from unittest.mock import patch

import pytest

from ailm.core.trend import TrendTracker


class TestEMA:
    def test_initial_value(self):
        t = TrendTracker(alpha=0.5, window_size=10)
        t.update("m", 100.0, slope_threshold=999)
        assert t.get_ema("m") == 100.0

    def test_converges_to_steady(self):
        t = TrendTracker(alpha=0.1, window_size=100)
        for _ in range(200):
            t.update("m", 50.0, slope_threshold=999)
        assert abs(t.get_ema("m") - 50.0) < 0.01

    def test_unknown_metric_returns_none(self):
        t = TrendTracker()
        assert t.get_ema("unknown") is None


class TestSlopeDetection:
    @patch("ailm.core.trend.time.monotonic")
    def test_rising_trend_alerts(self, mock_time):
        t = TrendTracker(alpha=0.5, window_size=20, cooldown_seconds=0)
        # Feed linearly increasing values over simulated time
        for i in range(40):
            mock_time.return_value = float(i * 60)  # 1 minute apart
            result = t.update("metric", float(i), slope_threshold=0.1)
        # Should eventually trigger an alert
        assert result is not None
        assert result.direction == "rising"
        assert result.slope > 0

    @patch("ailm.core.trend.time.monotonic")
    def test_falling_trend_alerts(self, mock_time):
        t = TrendTracker(alpha=0.5, window_size=20, cooldown_seconds=0)
        for i in range(40):
            mock_time.return_value = float(i * 60)
            result = t.update("metric", 100.0 - float(i), slope_threshold=0.1)
        assert result is not None
        assert result.direction == "falling"
        assert result.slope < 0

    @patch("ailm.core.trend.time.monotonic")
    def test_flat_line_no_alert(self, mock_time):
        t = TrendTracker(alpha=0.5, window_size=20, cooldown_seconds=0)
        for i in range(40):
            mock_time.return_value = float(i * 60)
            result = t.update("metric", 50.0, slope_threshold=1.0)
        assert result is None

    @patch("ailm.core.trend.time.monotonic")
    def test_window_not_full_no_alert(self, mock_time):
        t = TrendTracker(alpha=0.5, window_size=100)
        # Only feed 10 samples — not enough for half-window
        for i in range(10):
            mock_time.return_value = float(i * 60)
            result = t.update("metric", float(i * 10), slope_threshold=0.01)
        assert result is None


class TestCooldown:
    @patch("ailm.core.trend.time.monotonic")
    def test_cooldown_suppresses_second_alert(self, mock_time):
        t = TrendTracker(alpha=0.5, window_size=10, cooldown_seconds=3600)
        alerts = []

        # Feed rising trend — all within cooldown window (30*60s = 1800s < 3600s)
        for i in range(30):
            mock_time.return_value = float(i * 60)
            result = t.update("m", float(i * 5), slope_threshold=0.01)
            if result:
                alerts.append(result)

        # Only first alert should fire, rest suppressed by 1hr cooldown
        assert len(alerts) == 1


class TestMultipleMetrics:
    @patch("ailm.core.trend.time.monotonic")
    def test_independent_tracking(self, mock_time):
        t = TrendTracker(alpha=0.5, window_size=20, cooldown_seconds=0)
        for i in range(40):
            mock_time.return_value = float(i * 60)
            t.update("disk", float(i), slope_threshold=999)  # rising but high threshold
            t.update("cpu", 50.0, slope_threshold=0.01)  # flat, low threshold

        assert t.get_ema("disk") is not None
        assert t.get_ema("cpu") is not None
        assert t.get_ema("disk") != t.get_ema("cpu")


class TestAlertSummary:
    @patch("ailm.core.trend.time.monotonic")
    def test_summary_format(self, mock_time):
        t = TrendTracker(alpha=0.5, window_size=20, cooldown_seconds=0)
        result = None
        for i in range(40):
            mock_time.return_value = float(i * 60)
            result = t.update("disk_usage_pct", float(i * 2), slope_threshold=0.01)
        assert result is not None
        assert "disk_usage_pct" in result.summary
        assert "rising" in result.summary


class TestValidation:
    def test_invalid_alpha(self):
        with pytest.raises(ValueError):
            TrendTracker(alpha=0.0)
        with pytest.raises(ValueError):
            TrendTracker(alpha=1.0)
