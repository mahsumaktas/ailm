"""Journald source tests — priority mapping, batcher."""

from datetime import datetime, timezone

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.journald import (
    JournalEntry,
    JournaldSource,
    priority_to_severity,
)


# --- Priority mapping ---


class TestPriorityMapping:
    def test_emerg_is_critical(self):
        assert priority_to_severity(0) == Severity.CRITICAL

    def test_err_is_critical(self):
        assert priority_to_severity(3) == Severity.CRITICAL

    def test_warning_is_warning(self):
        assert priority_to_severity(4) == Severity.WARNING

    def test_notice_is_info(self):
        assert priority_to_severity(5) == Severity.INFO

    def test_info_is_info(self):
        assert priority_to_severity(6) == Severity.INFO

    def test_unknown_is_info(self):
        assert priority_to_severity(99) == Severity.INFO


# --- Source creation ---


class TestJournaldSource:
    def test_invalid_batch_seconds(self):
        with pytest.raises(ValueError):
            JournaldSource(batch_seconds=0)

    def test_defaults(self):
        source = JournaldSource()
        assert source.name == "journald"
