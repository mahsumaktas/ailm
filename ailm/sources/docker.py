"""Docker container event source — monitors container lifecycle events.

Uses create_subprocess_exec with fixed arguments (no shell, no user input).
Gracefully disabled when docker is not available.
"""

import asyncio
import logging

from ailm.core.bus import EventBus
from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import cancel_task

logger = logging.getLogger(__name__)

# Container actions that matter (skip noisy exec_create/exec_start/exec_die)
_IMPORTANT_ACTIONS = frozenset({
    "start", "stop", "die", "kill", "pause", "unpause",
    "oom", "restart", "create", "destroy",
})

_ACTION_SEVERITY = {
    "die": Severity.WARNING,
    "kill": Severity.WARNING,
    "oom": Severity.CRITICAL,
    "destroy": Severity.WARNING,
}


class DockerSource:
    """Stream docker events and publish container lifecycle events.

    All subprocess calls use create_subprocess_exec with hardcoded
    arguments — no shell invocation, no user input interpolation.
    """

    name = "docker"

    def __init__(self) -> None:
        self._bus: EventBus | None = None
        self._task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None

    @property
    def bus(self) -> EventBus:
        if self._bus is None:
            raise RuntimeError(f"Source '{self.name}' not started — call start() first")
        return self._bus

    async def start(self, bus: EventBus) -> None:
        if not await self._docker_available():
            logger.info("Docker not available, docker source disabled")
            return
        self._bus = bus
        self._task = asyncio.create_task(self._watch_events())

    async def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process = None
        await cancel_task(self._task)
        self._task = None

    async def _docker_available(self) -> bool:
        """Check if docker daemon is reachable (fixed args, no user input)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await asyncio.wait_for(proc.wait(), timeout=5) == 0
        except (OSError, asyncio.TimeoutError):
            return False

    async def _watch_events(self) -> None:
        """Long-running: stream 'docker events' with fixed format/filter flags."""
        while True:
            try:
                self._process = await asyncio.create_subprocess_exec(
                    "docker", "events",
                    "--format", "{{.Type}} {{.Action}} {{.Actor.Attributes.name}}",
                    "--filter", "type=container",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                async for line in self._process.stdout:
                    text = line.decode().strip()
                    if not text:
                        continue
                    await self._handle_event(text)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("Docker events stream error, retrying in 30s")
                await asyncio.sleep(30)

    async def _handle_event(self, line: str) -> None:
        """Parse a docker event line and publish if important."""
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            return
        action, container = parts[1], parts[2]

        if action not in _IMPORTANT_ACTIONS:
            return

        severity = _ACTION_SEVERITY.get(action, Severity.INFO)
        event = SystemEvent(
            type=EventType.SYSTEM_METRIC,
            severity=severity,
            raw_data=f"container={container} action={action}",
            source=self.name,
            summary=f"docker: {container} {action}",
        )
        await self.bus.publish(event)
