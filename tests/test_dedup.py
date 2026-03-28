"""Tests for EventDedup — fingerprinting, suppression, baseline, rate limiting."""

import time
from unittest.mock import patch

import pytest

from ailm.core.dedup import (
    DedupAction,
    DedupConfig,
    EventDedup,
    fingerprint,
    normalize_message,
)


class TestNormalize:
    def test_strips_pid(self):
        assert "[*]" in normalize_message("chrome[12345]: VAAPI error")

    def test_strips_hex(self):
        assert "0x*" in normalize_message("segfault at 0xdeadbeef")

    def test_strips_uuid(self):
        assert "<UUID>" in normalize_message("session a1b2c3d4-e5f6-7890-abcd-ef1234567890 started")

    def test_strips_large_numbers(self):
        assert "*" in normalize_message("port 45678 connection refused")

    def test_preserves_short_numbers(self):
        # 3 digits or less should stay
        result = normalize_message("error 404 not found")
        assert "404" in result

    def test_same_message_different_pid(self):
        a = normalize_message("chrome[1234]: GPU error")
        b = normalize_message("chrome[5678]: GPU error")
        assert a == b

    def test_chromium_log_prefix_stripped(self):
        a = normalize_message("[66:212:0328/063400.483036:ERROR:display_embedder.cc] VAAPI error")
        b = normalize_message("[66:212:0329/120000.000000:ERROR:display_embedder.cc] VAAPI error")
        assert a == b


class TestFingerprint:
    def test_deterministic(self):
        fp1 = fingerprint("journald", "chrome.service", "VAAPI error")
        fp2 = fingerprint("journald", "chrome.service", "VAAPI error")
        assert fp1 == fp2

    def test_different_messages(self):
        fp1 = fingerprint("journald", "chrome.service", "VAAPI error")
        fp2 = fingerprint("journald", "chrome.service", "OOM killed")
        assert fp1 != fp2

    def test_same_message_different_pid(self):
        fp1 = fingerprint("journald", "chrome.service", "chrome[1234]: error")
        fp2 = fingerprint("journald", "chrome.service", "chrome[5678]: error")
        assert fp1 == fp2

    def test_length(self):
        fp = fingerprint("journald", "test", "hello")
        assert len(fp) == 16


class TestShouldPublish:
    def test_first_occurrence_emits(self):
        dedup = EventDedup(DedupConfig(window_seconds=60))
        decision = dedup.should_publish("fp1", "journald")
        assert decision.action == DedupAction.EMIT
        assert decision.suppressed_count == 0

    def test_repeat_within_window_suppresses(self):
        dedup = EventDedup(DedupConfig(window_seconds=60))
        dedup.should_publish("fp1", "journald")
        decision = dedup.should_publish("fp1", "journald")
        assert decision.action == DedupAction.SUPPRESS

    def test_different_fingerprint_emits(self):
        dedup = EventDedup(DedupConfig(window_seconds=60))
        dedup.should_publish("fp1", "journald")
        decision = dedup.should_publish("fp2", "journald")
        assert decision.action == DedupAction.EMIT

    @patch("ailm.core.dedup.time.monotonic")
    def test_baseline_forces_emit(self, mock_time):
        mock_time.return_value = 0.0
        dedup = EventDedup(DedupConfig(window_seconds=10, baseline_seconds=60, aggregate_threshold=999))
        dedup.should_publish("fp1", "src")

        # 50 repeats within window
        for i in range(50):
            mock_time.return_value = float(i + 1)
            dedup.should_publish("fp1", "src")

        # After baseline interval
        mock_time.return_value = 61.0
        decision = dedup.should_publish("fp1", "src")
        assert decision.action == DedupAction.EMIT
        assert decision.suppressed_count > 0

    @patch("ailm.core.dedup.time.monotonic")
    def test_window_expiry_re_emits(self, mock_time):
        mock_time.return_value = 0.0
        dedup = EventDedup(DedupConfig(window_seconds=10, baseline_seconds=300))
        dedup.should_publish("fp1", "src")

        mock_time.return_value = 5.0
        assert dedup.should_publish("fp1", "src").action == DedupAction.SUPPRESS

        mock_time.return_value = 15.0  # past window
        assert dedup.should_publish("fp1", "src").action == DedupAction.EMIT


class TestRateLimit:
    @patch("ailm.core.dedup.time.monotonic")
    def test_under_limit(self, mock_time):
        mock_time.return_value = 0.0
        dedup = EventDedup(DedupConfig(max_per_source_per_minute=5))
        for i in range(5):
            d = dedup.should_publish(f"fp{i}", "src")
            assert d.action == DedupAction.EMIT

    @patch("ailm.core.dedup.time.monotonic")
    def test_over_limit(self, mock_time):
        mock_time.return_value = 0.0
        dedup = EventDedup(DedupConfig(max_per_source_per_minute=3))
        for i in range(3):
            dedup.should_publish(f"fp{i}", "src")

        # 4th should be suppressed (rate limit)
        d = dedup.should_publish("fp_new", "src")
        assert d.action == DedupAction.SUPPRESS

    @patch("ailm.core.dedup.time.monotonic")
    def test_different_sources_independent(self, mock_time):
        mock_time.return_value = 0.0
        dedup = EventDedup(DedupConfig(max_per_source_per_minute=2))
        dedup.should_publish("fp1", "src_a")
        dedup.should_publish("fp2", "src_a")

        # src_a is at limit, but src_b is fresh
        d = dedup.should_publish("fp3", "src_b")
        assert d.action == DedupAction.EMIT


class TestPrune:
    @patch("ailm.core.dedup.time.monotonic")
    def test_stale_fingerprints_removed(self, mock_time):
        mock_time.return_value = 0.0
        dedup = EventDedup(DedupConfig(baseline_seconds=30))
        dedup.should_publish("fp1", "src")
        assert dedup.tracked_count == 1

        # After 2x baseline, prune kicks in
        mock_time.return_value = 61.0
        dedup.should_publish("fp_new", "src")  # triggers prune
        assert dedup.tracked_count == 1  # fp1 pruned, fp_new added


class TestBulkSuppression:
    """Simulate the Chrome VAAPI flood scenario."""

    def test_100_identical_messages_produce_1_event(self):
        dedup = EventDedup(DedupConfig(window_seconds=60, baseline_seconds=300))
        emitted = 0
        for _ in range(100):
            fp = fingerprint("journald", "chrome.service", "VAAPI error on display")
            d = dedup.should_publish(fp, "journald")
            if d.action == DedupAction.EMIT:
                emitted += 1
        assert emitted == 1  # only the first one
