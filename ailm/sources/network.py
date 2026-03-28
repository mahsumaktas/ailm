"""Tailscale mesh network health monitoring.

Uses 'tailscale status --json' via create_subprocess_exec
with fixed arguments. No shell, no user input.
Gracefully disabled when tailscale is not available.
"""

import asyncio
import json
import logging

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)


class TailscaleSource(PollingSource):
    """Poll tailscale peer status and publish connect/disconnect events.

    All subprocess calls use create_subprocess_exec with hardcoded args.
    """

    name = "tailscale"

    def __init__(self, interval: int = 60) -> None:
        super().__init__(interval)
        self._available = False
        self._known_peers: dict[str, bool] = {}

    async def start(self, bus) -> None:
        self._available = await self._check_tailscale()
        if not self._available:
            logger.info("Tailscale not available, network source disabled")
            return
        await super().start(bus)

    async def _check_tailscale(self) -> bool:
        """Check if tailscale CLI works (hardcoded args, safe)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "tailscale", "status", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            data = json.loads(stdout.decode())
            return "Peer" in data or "Self" in data
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
            return False

    async def check(self) -> None:
        if not self._available:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "tailscale", "status", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            data = json.loads(stdout.decode())
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
            return

        peers = data.get("Peer", {})
        current: dict[str, bool] = {}
        for info in peers.values():
            hostname = info.get("HostName", "unknown")
            current[hostname] = info.get("Online", False)

        if not self._known_peers:
            self._known_peers = current
            online = sum(1 for v in current.values() if v)
            logger.info("Tailscale: %d peers (%d online)", len(current), online)
            return

        for hostname, online in current.items():
            was = self._known_peers.get(hostname)
            if was is None:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.INFO,
                    raw_data=f"peer={hostname} online={online}",
                    source=self.name,
                    summary=f"tailscale: new peer {hostname}",
                ))
            elif was and not online:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                    raw_data=f"peer={hostname} online=false",
                    source=self.name,
                    summary=f"tailscale: {hostname} went offline",
                ))
            elif not was and online:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.INFO,
                    raw_data=f"peer={hostname} online=true",
                    source=self.name,
                    summary=f"tailscale: {hostname} came online",
                ))

        self._known_peers = current
