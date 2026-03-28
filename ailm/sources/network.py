"""Network health monitoring — Tailscale peers, services, ports.

Uses create_subprocess_exec and socket for all checks.
No shell invocation, no user input interpolation.
"""

import asyncio
import json
import logging
import socket

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


class ServicePortSource(PollingSource):
    """Monitor user services and local ports. Fixed args only."""

    name = "netcheck"

    def __init__(
        self,
        interval: int = 60,
        services: list[tuple[str, bool]] | None = None,
        ports: list[tuple[int, str]] | None = None,
    ) -> None:
        super().__init__(interval)
        self._services = services or [("sunshine", True)]
        self._ports = ports or [
            (22, "sshd"), (11434, "ollama"), (47984, "sunshine-https"),
        ]
        self._service_state: dict[str, bool] = {}
        self._port_state: dict[int, bool] = {}

    async def check(self) -> None:
        for unit, is_user in self._services:
            active = await self._is_active(unit, is_user)
            was = self._service_state.get(unit)
            if was is not None and was and not active:
                await self.bus.publish(SystemEvent(
                    type=EventType.SERVICE_FAIL, severity=Severity.WARNING,
                    raw_data=f"service={unit} active=false user={is_user}",
                    source=self.name, summary=f"{unit}.service went down",
                ))
            elif was is not None and not was and active:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.INFO,
                    raw_data=f"service={unit} active=true",
                    source=self.name, summary=f"{unit}.service recovered",
                ))
            self._service_state[unit] = active

        for port, label in self._ports:
            up = await asyncio.to_thread(self._check_port, port)
            was = self._port_state.get(port)
            if was is not None and was and not up:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                    raw_data=f"port={port} label={label} reachable=false",
                    source=self.name, summary=f"Port {port} ({label}) unreachable",
                ))
            self._port_state[port] = up

    async def _is_active(self, unit: str, is_user: bool) -> bool:
        """Check systemd service (hardcoded args, safe)."""
        try:
            args = ["systemctl"]
            if is_user:
                args.append("--user")
            args.extend(["is-active", unit])
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip() == "active"
        except (OSError, asyncio.TimeoutError):
            return False

    @staticmethod
    def _check_port(port: int, host: str = "127.0.0.1") -> bool:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except (OSError, ConnectionRefusedError):
            return False
