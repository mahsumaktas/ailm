"""LLM task queue for graceful degradation.

When Ollama is unavailable, tasks are queued here and drained
when the service comes back online. Tasks older than MAX_AGE are discarded.
"""

import logging
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ailm.llm.client import OllamaClient

logger = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=1)
MAX_QUEUE_SIZE = 500

type ResultCallback = Callable[[str], Coroutine[Any, Any, None]]


@dataclass
class LLMTask:
    prompt: str
    system: str | None = None
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    callback: ResultCallback | None = None


class LLMTaskQueue:
    def __init__(self, maxlen: int = MAX_QUEUE_SIZE) -> None:
        self._tasks: deque[LLMTask] = deque(maxlen=maxlen)

    def enqueue(self, task: LLMTask) -> None:
        self._tasks.append(task)

    async def drain(self, client: OllamaClient) -> int:
        """Process pending tasks. Returns count of successfully processed tasks."""
        processed = 0
        now = datetime.now(timezone.utc)

        while self._tasks:
            task = self._tasks[0]

            # Discard stale tasks
            if now - task.created > MAX_AGE:
                self._tasks.popleft()
                logger.debug("Discarded stale LLM task (age: %s)", now - task.created)
                continue

            result = await client.generate(task.prompt, task.system)
            if result is None:
                break  # client went unavailable again

            self._tasks.popleft()
            if task.callback is not None:
                await task.callback(result)
            processed += 1

        return processed

    @property
    def pending(self) -> int:
        return len(self._tasks)

    def clear(self) -> None:
        self._tasks.clear()
