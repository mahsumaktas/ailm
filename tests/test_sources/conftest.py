"""Shared fixtures for source tests."""

import pytest

from ailm.core.bus import EventBus
from ailm.core.models import SystemEvent


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def events(bus: EventBus) -> list[SystemEvent]:
    received: list[SystemEvent] = []
    bus.subscribe(None, received.append)
    return received
