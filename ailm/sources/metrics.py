"""Unified system metrics collector.

Replaces 7+ individual sources with a single 30s poll cycle.
All subprocess calls use create_subprocess_exec — no shell, no user input.
"""

import asyncio
import logging
import re
import time
from pathlib import Path

import psutil

from ailm.core.models import EventType, Severity, SystemEvent
from ailm.core.trend import TrendTracker
from ailm.sources.base import PollingSource

logger = logging.getLogger(__name__)

_HWMON = Path("/sys/class/hwmon")
_PSI = Path("/proc/pressure")
_DISKSTATS = Path("/proc/diskstats")
_TEMP_THRESH = {"k10temp": 90, "nct6799": 85, "spd5118": 55, "amdgpu": 95, "nvme": 70}
_PSI_THRESH = {"cpu": 50.0, "memory": 20.0, "io": 80.0}
_SMART_RE = {
    "spare": re.compile(r"Available Spare:\s+(\d+)%"),
    "spare_threshold": re.compile(r"Available Spare Threshold:\s+(\d+)%"),
    "media_errors": re.compile(r"Media and Data Integrity Errors:\s+(\d+)"),
}
_BTRFS_RE = re.compile(r"\[(/dev/\S+)\]\.(\w+)\s+(\d+)")


def _read_int(p: Path) -> int | None:
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


def _hwmon_temps() -> list[tuple[str, str, float]]:
    out = []
    for d in (_HWMON.iterdir() if _HWMON.exists() else []):
        try:
            name = (d / "name").read_text().strip()
        except OSError:
            continue
        for f in sorted(d.glob("temp*_input")):
            v = _read_int(f)
            if v is None:
                continue
            lf = f.with_name(f.name.replace("_input", "_label"))
            try:
                label = lf.read_text().strip() if lf.exists() else f.stem
            except OSError:
                label = f.stem
            out.append((name, label, v / 1000.0))
    return out


def _psi_avg10(resource: str) -> float | None:
    try:
        text = (_PSI / resource).read_text()
    except (OSError, PermissionError):
        return None
    for line in text.splitlines():
        if line.startswith("some "):
            for kv in line.split():
                if kv.startswith("avg10="):
                    return float(kv[6:])
    return None


class MetricsCollector(PollingSource):
    """All system metrics in one 30s poll. create_subprocess_exec only."""

    name = "metrics"

    def __init__(
        self, interval: int = 30, trend: TrendTracker | None = None,
        disk_warn: int = 80, disk_crit: int = 95,
    ) -> None:
        super().__init__(interval)
        self._trend = trend
        self._disk_warn = disk_warn
        self._disk_crit = disk_crit
        self._tick = 0
        self._has_nvidia = False
        self._has_psi = _PSI.exists()
        self._smart_devs: list[str] = []
        self._alerts: dict[str, bool] = {}
        self._prev_net = {"recv": 0, "sent": 0, "t": 0.0}
        self._prev_dio: dict | None = None
        self._prev_dio_t = 0.0
        self._prev_smart: dict[str, dict] = {}
        self._prev_btrfs: dict[str, int] = {}

    async def start(self, bus) -> None:
        self._has_nvidia = await self._probe("nvidia-smi", "--query-gpu=name", "--format=csv,noheader")
        for dev in ("/dev/nvme0", "/dev/nvme1"):
            if Path(dev).exists():
                self._smart_devs.append(dev)
        await super().start(bus)

    async def _probe(self, *args) -> bool:
        p = None
        try:
            p = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await asyncio.wait_for(p.communicate(), timeout=5)
            return bool(stdout.decode().strip())
        except asyncio.TimeoutError:
            if p is not None:
                p.kill()
            return False
        except OSError:
            return False

    async def check(self) -> None:
        self._tick += 1
        # Every 30s
        await self._cpu_ram_swap()
        await self._disk_usage()
        await self._network()
        await self._psi()
        await self._temps()
        await self._disk_io()
        await self._processes()
        if self._has_nvidia:
            await self._nvidia()
        # Sub-intervals
        if self._tick % 10 == 0:
            await self._btrfs()
        if self._tick % 120 == 0:
            await self._smart()

    async def _cpu_ram_swap(self) -> None:
        cpu = psutil.cpu_percent(interval=0)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        t = self._trend
        if t:
            t.update("cpu_pct", cpu, slope_threshold=20.0)
            t.update("ram_pct", mem.percent, slope_threshold=10.0)
            t.update("swap_pct", swap.percent, slope_threshold=5.0)
            if mem.percent > 70:
                a = t.update("_ram_proj", mem.percent, slope_threshold=10.0)
                if a and a.slope > 0:
                    mins = (100.0 - mem.percent) / a.slope * 60
                    if mins < 60:
                        sev = Severity.CRITICAL if mins < 15 else Severity.WARNING
                        await self.bus.publish(SystemEvent(
                            type=EventType.TREND_ALERT, severity=sev,
                            raw_data=f"ram={mem.percent:.0f}% mins={mins:.0f}",
                            source=self.name,
                            summary=f"RAM {mem.percent:.0f}% — OOM in {mins:.0f} min",
                        ))

    async def _disk_usage(self) -> None:
        pct = psutil.disk_usage("/").percent
        if self._trend:
            a = self._trend.update("disk_pct", pct, slope_threshold=0.5)
            if a and a.slope > 0:
                hrs = (100.0 - pct) / a.slope
                if hrs < 72:
                    await self.bus.publish(SystemEvent(
                        type=EventType.TREND_ALERT, severity=Severity.WARNING,
                        raw_data=f"disk={pct:.0f}% hrs={hrs:.0f}",
                        source=self.name, summary=f"Disk full in {hrs/24:.1f} days",
                    ))
        if pct >= self._disk_crit and not self._alerts.get("dc"):
            self._alerts["dc"] = True
            await self.bus.publish(SystemEvent(
                type=EventType.DISK_ALERT, severity=Severity.CRITICAL,
                raw_data=f"pct={pct}", source=self.name, summary=f"Disk {pct:.0f}%"))
        elif pct >= self._disk_warn:
            # Disk dropped from critical to warning range — clear "dc" so a
            # future critical breach can re-alert instead of staying silenced.
            self._alerts["dc"] = False
            if not self._alerts.get("dw"):
                self._alerts["dw"] = True
                await self.bus.publish(SystemEvent(
                    type=EventType.DISK_ALERT, severity=Severity.WARNING,
                    raw_data=f"pct={pct}", source=self.name, summary=f"Disk {pct:.0f}%"))
        else:
            self._alerts["dc"] = self._alerts["dw"] = False

    async def _network(self) -> None:
        net = psutil.net_io_counters()
        now = time.monotonic()
        dt = now - self._prev_net["t"] if self._prev_net["t"] > 0 else 30.0
        if dt > 0 and self._prev_net["t"] > 0 and self._trend:
            r = (net.bytes_recv - self._prev_net["recv"]) * 8 / dt / 1e6
            s = (net.bytes_sent - self._prev_net["sent"]) * 8 / dt / 1e6
            self._trend.update("net_recv_mbps", r, slope_threshold=50.0)
            self._trend.update("net_sent_mbps", s, slope_threshold=50.0)
        self._prev_net = {"recv": net.bytes_recv, "sent": net.bytes_sent, "t": now}

    async def _processes(self) -> None:
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                rss = p.info["memory_info"].rss
                if rss > 500_000_000:
                    procs.append((p.info["pid"], p.info["name"], rss))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: -x[2])
        alerted_now: set[str] = set()
        for pid, name, rss in procs[:5]:
            gb = rss / (1024**3)
            if self._trend:
                self._trend.update(f"proc_{name}_gb", gb, slope_threshold=1.0)
            if gb > 10:
                alerted_now.add(name)
                if not self._alerts.get(f"proc_{name}"):
                    self._alerts[f"proc_{name}"] = True
                    await self.bus.publish(SystemEvent(
                        type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                        raw_data=f"pid={pid} name={name} gb={gb:.1f}",
                        source=self.name,
                        summary=f"{name} (PID {pid}) using {gb:.1f} GB RAM",
                    ))
        # Clear alert when process drops below threshold
        for key in [k for k in self._alerts if k.startswith("proc_") and k[5:] not in alerted_now]:
            self._alerts[key] = False

    async def _psi(self) -> None:
        if not self._has_psi:
            return
        for res in ("cpu", "memory", "io"):
            v = _psi_avg10(res)
            if v is None:
                continue
            if self._trend:
                self._trend.update(f"psi_{res}", v, slope_threshold=10.0)
            th = _PSI_THRESH.get(res, 50.0)
            k = f"psi_{res}"
            if v >= th and not self._alerts.get(k):
                self._alerts[k] = True
                sev = Severity.CRITICAL if res == "memory" else Severity.WARNING
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=sev,
                    raw_data=f"psi_{res}={v:.1f}",
                    source=self.name, summary=f"{res} pressure {v:.0f}%",
                ))
            elif v < th * 0.7:
                self._alerts[k] = False

    async def _temps(self) -> None:
        for name, label, c in _hwmon_temps():
            if self._trend:
                self._trend.update(f"t_{name}_{label}", c, slope_threshold=5.0)
            th = _TEMP_THRESH.get(name, 85)
            k = f"t_{name}_{label}"
            if c >= th and not self._alerts.get(k):
                self._alerts[k] = True
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC,
                    severity=Severity.CRITICAL if c >= th + 10 else Severity.WARNING,
                    raw_data=f"{name}/{label}={c:.0f}C",
                    source=self.name, summary=f"{name}/{label} at {c:.0f}C",
                ))
            elif c < th - 5:
                self._alerts[k] = False

    async def _nvidia(self) -> None:
        p = None
        try:
            p = await asyncio.create_subprocess_exec(
                "nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total,power.draw",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=5)
        except asyncio.TimeoutError:
            if p:
                p.kill()
            return
        except OSError:
            return
        parts = [x.strip() for x in out.decode().strip().split(",")]
        if len(parts) < 4:
            return
        try:
            temp, vu, vt, _ = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            return
        vpct = (vu / vt * 100) if vt > 0 else 0
        if self._trend:
            self._trend.update("gpu_temp", temp, slope_threshold=5.0)
            self._trend.update("gpu_vram_pct", vpct, slope_threshold=10.0)
        if vpct >= 90 and not self._alerts.get("gv"):
            self._alerts["gv"] = True
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.WARNING,
                raw_data=f"vram={vpct:.0f}%", source=self.name,
                summary=f"GPU VRAM {vpct:.0f}% ({vu:.0f}/{vt:.0f} MB)",
            ))
        elif vpct < 85:
            self._alerts["gv"] = False
        if temp >= 85 and not self._alerts.get("gt"):
            self._alerts["gt"] = True
            await self.bus.publish(SystemEvent(
                type=EventType.SYSTEM_METRIC, severity=Severity.CRITICAL,
                raw_data=f"gpu_temp={temp}", source=self.name,
                summary=f"GPU {temp}C critical",
            ))
        elif temp < 80:
            self._alerts["gt"] = False

    async def _disk_io(self) -> None:
        try:
            text = _DISKSTATS.read_text()
        except OSError:
            return
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 14 and parts[2] == "nvme0n1":
                io_ms = int(parts[12])
                now = time.monotonic()
                if self._prev_dio and self._prev_dio_t > 0:
                    dt = now - self._prev_dio_t
                    if dt > 0:
                        util = (io_ms - self._prev_dio["ms"]) / (dt * 1000) * 100
                        if self._trend:
                            self._trend.update("io_util", util, slope_threshold=20.0)
                self._prev_dio = {"ms": io_ms}
                self._prev_dio_t = now
                break

    async def _smart(self) -> None:
        for dev in self._smart_devs:
            p = None
            try:
                p = await asyncio.create_subprocess_exec(
                    "smartctl", "-a", dev,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(p.communicate(), timeout=15)
            except asyncio.TimeoutError:
                if p is not None:
                    p.kill()
                continue
            except OSError:
                continue
            vals = {}
            for k, pat in _SMART_RE.items():
                m = pat.search(out.decode())
                if m:
                    vals[k] = int(m.group(1))
            me = vals.get("media_errors", 0)
            prev_me = self._prev_smart.get(dev, {}).get("media_errors", 0)
            if dev in self._prev_smart and me > prev_me:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.CRITICAL,
                    raw_data=f"dev={dev} errors={me}", source=self.name,
                    summary=f"NVMe {dev}: {me-prev_me} new media errors",
                ))
            sp = vals.get("spare", 100)
            st = vals.get("spare_threshold", 5)
            if sp <= st:
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=Severity.CRITICAL,
                    raw_data=f"dev={dev} spare={sp}%", source=self.name,
                    summary=f"NVMe {dev}: spare {sp}% — failing",
                ))
            self._prev_smart[dev] = vals

    async def _btrfs(self) -> None:
        p = None
        try:
            p = await asyncio.create_subprocess_exec(
                "btrfs", "device", "stats", "/",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), timeout=10)
        except asyncio.TimeoutError:
            if p is not None:
                p.kill()
            return
        except OSError:
            return
        for m in _BTRFS_RE.finditer(out.decode()):
            k, v = f"{m.group(1)}.{m.group(2)}", int(m.group(3))
            prev = self._prev_btrfs.get(k, 0)
            if k in self._prev_btrfs and v > prev:
                sev = Severity.CRITICAL if "corruption" in k else Severity.WARNING
                await self.bus.publish(SystemEvent(
                    type=EventType.SYSTEM_METRIC, severity=sev,
                    raw_data=f"btrfs {k}={v}", source=self.name,
                    summary=f"btrfs: {v-prev} new {m.group(2)}",
                ))
            self._prev_btrfs[k] = v
