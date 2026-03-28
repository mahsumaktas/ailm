"""Security CVE monitoring via arch-audit.

Uses 'arch-audit' via create_subprocess_exec — zero arguments,
no shell, no user input. Gracefully disabled when not installed.
"""

import asyncio
import logging
import re

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_AUDIT_RE = re.compile(r"^(\S+) is affected by (.+)\. (\w+) risk!$")
_RISK_SEVERITY = {
    "Critical": Severity.CRITICAL, "High": Severity.CRITICAL,
    "Medium": Severity.WARNING, "Low": Severity.INFO,
}


class SecuritySource(PollingSource):
    """Daily arch-audit scan for CVEs. create_subprocess_exec only."""

    name = "security"

    def __init__(self, interval: int = 86400) -> None:
        super().__init__(interval)
        self._available = False
        self._known_vulns: set[str] = set()

    async def start(self, bus) -> None:
        self._available = await self._check()
        if not self._available:
            logger.info("arch-audit not available, security source disabled")
            return
        await super().start(bus)

    async def _check(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "arch-audit", "--help",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def check(self) -> None:
        if not self._available:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "arch-audit",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except (OSError, asyncio.TimeoutError):
            return

        current: set[str] = set()
        for line in stdout.decode().splitlines():
            m = _AUDIT_RE.match(line.strip())
            if not m:
                continue
            pkg, desc, risk = m.group(1), m.group(2), m.group(3)
            current.add(pkg)
            if pkg in self._known_vulns:
                continue
            severity = _RISK_SEVERITY.get(risk, Severity.INFO)
            await self.bus.publish(SystemEvent(
                type=EventType.CONFIG_CHANGE, severity=severity,
                raw_data=f"package={pkg} risk={risk} desc={desc}",
                source=self.name,
                summary=f"CVE: {pkg} — {desc} ({risk} risk)",
            ))

        self._known_vulns = current
        if current:
            logger.info("arch-audit: %d vulnerable packages", len(current))
