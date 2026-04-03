"""Unified external services collector.

Docker stream + tailscale + services + security + orphan + pacnew + coredump.
All subprocess: create_subprocess_exec with fixed args. No shell, no user input.
"""

import asyncio
import json
import logging

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource, cancel_task

logger = logging.getLogger(__name__)

_DOCKER_ACTIONS = frozenset({"start", "stop", "die", "kill", "oom", "restart", "destroy"})
_DOCKER_SEV = {"die": Severity.WARNING, "kill": Severity.WARNING, "oom": Severity.CRITICAL}
_SERVICES = [("sunshine", True)]
_PORTS = [(22, "sshd"), (11434, "ollama"), (47984, "sunshine")]


class ExternalCollector(PollingSource):
    """All external checks in one source. create_subprocess_exec only."""

    name = "external"

    def __init__(self, interval: int = 60) -> None:
        super().__init__(interval)
        self._tick = 0
        self._docker_task: asyncio.Task | None = None
        self._has_docker = self._has_tailscale = self._has_audit = self._has_coredump = False
        self._peers: dict[str, bool] = {}
        self._svc_state: dict[str, bool] = {}
        self._port_state: dict[int, bool] = {}
        self._known_vulns: set[str] = set()
        self._known_dumps: set[str] = set()
        self._known_pacnew: set[str] | None = None
        self._orphan_count: int | None = None
        self._first_scan = True

    async def start(self, bus) -> None:
        self._has_docker = await self._probe("docker", "info")
        self._has_tailscale = await self._probe("tailscale", "status", "--json")
        self._has_audit = await self._probe("arch-audit", "--help")
        self._has_coredump = await self._probe("coredumpctl", "list", "--no-pager")
        if self._has_docker:
            self._bus = bus  # need bus before super().start for docker stream
            self._docker_task = asyncio.create_task(self._docker_stream())
        await super().start(bus)

    async def stop(self) -> None:
        await cancel_task(self._docker_task)
        self._docker_task = None
        await super().stop()

    async def _probe(self, *args) -> bool:
        try:
            p = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            return await asyncio.wait_for(p.wait(), timeout=5) == 0
        except (OSError, asyncio.TimeoutError):
            return False

    async def check(self) -> None:
        self._tick += 1
        if self._has_tailscale:
            await self._tailscale()
        await self._services_ports()
        if self._has_coredump:
            await self._coredumps()
        if self._tick % 60 == 0:
            await self._pacnew()
            if self._has_audit:
                await self._security()
            await self._orphans()

    async def _docker_stream(self) -> None:
        """Long-running docker event stream. Hardcoded format/filter flags."""
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "events", "--format",
                    "{{.Type}} {{.Action}} {{.Actor.Attributes.name}}",
                    "--filter", "type=container",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                async for line in proc.stdout:
                    parts = line.decode().strip().split(maxsplit=2)
                    if len(parts) < 3:
                        continue
                    action, container = parts[1], parts[2]
                    if action not in _DOCKER_ACTIONS:
                        continue
                    await self.bus.publish(SystemEvent(
                        type=EventType.SYSTEM_METRIC,
                        severity=_DOCKER_SEV.get(action, Severity.INFO),
                        raw_data=f"container={container} action={action}",
                        source="docker", summary=f"docker: {container} {action}",
                    ))
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(30)

    async def _tailscale(self) -> None:
        try:
            p = await asyncio.create_subprocess_exec(
                "tailscale", "status", "--json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=10)
            data = json.loads(out.decode())
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
            return
        cur = {v.get("HostName", "?"): v.get("Online", False)
               for v in data.get("Peer", {}).values()}
        if not self._peers:
            self._peers = cur
            return
        for h, on in cur.items():
            was = self._peers.get(h)
            if was and not on:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                    raw_data=f"peer={h}", source="tailscale",
                    summary=f"tailscale: {h} offline",
                ))
            elif was is not None and not was and on:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.INFO,
                    raw_data=f"peer={h}", source="tailscale",
                    summary=f"tailscale: {h} online",
                ))
        self._peers = cur

    async def _services_ports(self) -> None:
        for unit, is_user in _SERVICES:
            args = ["systemctl"] + (["--user"] if is_user else []) + ["is-active", unit]
            try:
                p = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(p.communicate(), timeout=5)
                active = out.decode().strip() == "active"
            except (OSError, asyncio.TimeoutError):
                active = False
            was = self._svc_state.get(unit)
            if was and not active:
                await self.bus.publish(SystemEvent(
                    type=EventType.SERVICE_FAIL, severity=Severity.WARNING,
                    raw_data=f"svc={unit}", source="netcheck",
                    summary=f"{unit}.service down",
                ))
            self._svc_state[unit] = active
        for port, label in _PORTS:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=2,
                )
                writer.close()
                await writer.wait_closed()
                up = True
            except (OSError, asyncio.TimeoutError):
                up = False
            was = self._port_state.get(port)
            if was and not up:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                    raw_data=f"port={port}", source="netcheck",
                    summary=f"Port {port} ({label}) down",
                ))
            self._port_state[port] = up

    async def _coredumps(self) -> None:
        try:
            p = await asyncio.create_subprocess_exec(
                "coredumpctl", "list", "--no-pager", "--since", "1 hour ago",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError):
            return
        for line in out.decode().splitlines():
            parts = line.split()
            pid = sig = exe = None
            for part in parts:
                if part.isdigit() and not pid:
                    pid = part
                elif part.startswith("SIG") and not sig:
                    sig = part
                elif "/" in part and pid:
                    exe = part
                    break
            if not pid or not sig:
                continue
            key = f"{pid}_{sig}"
            if key in self._known_dumps:
                continue
            if self._first_scan:
                self._known_dumps.add(key)
                continue
            self._known_dumps.add(key)
            name = (exe or "unknown").split("/")[-1]
            sev = Severity.CRITICAL if sig in ("SIGSEGV", "SIGBUS") else Severity.WARNING
            await self.bus.publish(SystemEvent(
                type=EventType.LOG_ANOMALY, severity=sev,
                raw_data=f"sig={sig} exe={exe}", source="coredump",
                summary=f"Crash: {name} ({sig})",
            ))
        self._first_scan = False

    async def _pacnew(self) -> None:
        try:
            p = await asyncio.create_subprocess_exec(
                "find", "/etc", "-name", "*.pacnew", "-type", "f",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=30)
        except (OSError, asyncio.TimeoutError):
            return
        cur = {line.strip() for line in out.decode().splitlines() if line.strip()}
        if self._known_pacnew is None:
            self._known_pacnew = cur
            return
        for path in sorted(cur - self._known_pacnew):
            await self.bus.publish(SystemEvent(
                type=EventType.CONFIG_CHANGE, severity=Severity.WARNING,
                raw_data=f"path={path}", source="pacnew",
                summary=f"New .pacnew: {path}",
            ))
        self._known_pacnew = cur

    async def _security(self) -> None:
        import re
        audit_re = re.compile(r"^(\S+) is affected by (.+)\. (\w+) risk!$")
        risk_map = {"Critical": Severity.CRITICAL, "High": Severity.CRITICAL,
                     "Medium": Severity.WARNING, "Low": Severity.INFO}
        try:
            p = await asyncio.create_subprocess_exec(
                "arch-audit",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=60)
        except (OSError, asyncio.TimeoutError):
            return
        cur: set[str] = set()
        for line in out.decode().splitlines():
            m = audit_re.match(line.strip())
            if not m:
                continue
            pkg, desc, risk = m.group(1), m.group(2), m.group(3)
            cur.add(pkg)
            if pkg not in self._known_vulns:
                await self.bus.publish(SystemEvent(
                    type=EventType.CONFIG_CHANGE, severity=risk_map.get(risk, Severity.INFO),
                    raw_data=f"pkg={pkg} risk={risk}", source="security",
                    summary=f"CVE: {pkg} — {desc} ({risk})",
                ))
        self._known_vulns = cur

    async def _orphans(self) -> None:
        try:
            p = await asyncio.create_subprocess_exec(
                "pacman", "-Qtd",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError):
            return
        lines = [line.strip() for line in out.decode().splitlines() if line.strip()]
        count = len(lines)
        if self._orphan_count is not None and count > self._orphan_count:
            new = count - self._orphan_count
            sample = ", ".join(line.split()[0] for line in lines[:5])
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.INFO,
                raw_data=f"orphans={count}", source="orphan",
                summary=f"{count} orphans ({new} new): {sample}",
            ))
        self._orphan_count = count
