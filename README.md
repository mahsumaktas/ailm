<div align="center">
  <h1>ailm</h1>
  <p><strong>AI-powered Linux system companion that watches your machine and tells you what matters.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/status-v0.3--dev-blue" />
    <img src="https://img.shields.io/badge/platform-Linux-blue" />
    <img src="https://img.shields.io/badge/LLM-batch--analysis-green" />
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" />
    <img src="https://img.shields.io/badge/GPU-5%25%20idle-brightgreen" />
    <img src="https://img.shields.io/badge/tests-492%20passing-brightgreen" />
    <img src="https://img.shields.io/badge/python-%3E%3D3.12-blue" />
  </p>
  <p>
    <a href="README.tr.md">Turkce</a> ·
    <a href="ROADMAP.md">Roadmap</a> ·
    <a href="VISION.md">Vision</a> ·
    <a href="docs/architecture.md">Architecture</a>
  </p>
</div>

---

## What is ailm?

ailm is a system tray daemon for Linux that monitors your machine, classifies system events with a local LLM, and gives you a morning briefing — all running on your own hardware.

Instead of you watching your system, **ailm watches it for you.**

```
┌─────────────────────────────────┐
│  ailm                        ─ □ │
│─────────────────────────────────│
│  🟢 System healthy              │
│  CPU 12% · RAM 34% · Disk 45%  │
│─────────────────────────────────│
│  📋 Today's briefing            │
│  09:00 ✓ Morning report: all OK │
│  11:42 ⚠ VAAPI allocation fail  │
│  14:20 ✓ 3 packages updated    │
│  15:01 · Service restart needed  │
└─────────────────────────────────┘
```

## Why ailm?

Rolling release distributions (Arch, CachyOS, EndeavourOS) are powerful but noisy.
Every day brings updates, service failures, disk pressure, kernel changes, log anomalies.
Most users either ignore these signals or spend too much time chasing them.

ailm sits in the middle: it reads the noise, understands the context via LLM, and surfaces only what actually matters — in plain language.

## Core Philosophy

- **Proactive over reactive.** ailm tells you before you ask.
- **Local by default.** Your logs never leave your machine.
- **Listener not doer.** ailm reads existing tools (snapper, pacman, systemd) — it doesn't replace them.
- **Learns over time.** The more you use it, the less noise you see.
- **Graceful degradation.** If Ollama is down, ailm still works — events queue, analysis waits.

## Features

### v0.1 — Morning Briefing

| Feature | Status |
|---|---|
| System tray icon (green/amber/red health status) | ✅ |
| Popup feed with LLM-classified event cards | ✅ |
| Morning briefing (daily digest at 06:00) | ✅ |
| journald log monitoring + regex pre-filter + LLM classification | ✅ |
| Package update tracking (pacman ALPM log parser) | ✅ |
| Snapshot event tracking (snapper/snap-pac watcher) | ✅ |
| Disk usage alerts with severity dedup | ✅ |
| Failed systemd service detection | ✅ |
| Reboot detection (kernel mismatch check) | ✅ |
| Safe actions whitelist (restart service, vacuum journal) | ✅ |
| pluggy hook system (event/status/action hooks) | ✅ |
| Graceful degradation (LLM queue + health check) | ✅ |
| systemd user service + control panel tray | ✅ |

### v0.2 — Resilience

| Feature | Status |
|---|---|
| Event dedup + rate limiting | ✅ |
| Crash-resilient ring buffer log (fdatasync) | ✅ |
| Boot crash detection (state file + log analysis) | ✅ |
| EMA trend detection with projections | ✅ |
| SIGHUP config hot-reload | ✅ |

### v0.3 — Sustainable Architecture (current)

Radical simplification: 20 sources → 3 collectors, per-event LLM → batch analysis.

| Feature | Status |
|---|---|
| **MetricsCollector** — all hardware in one 30s poll | ✅ |
| CPU, RAM, swap, disk, network, PSI pressure | ✅ |
| NVIDIA GPU (temp, VRAM, power, PCIe) | ✅ |
| All hwmon sensors (VRM, chipset, RAM, WiFi temps) | ✅ |
| NVMe SMART health + Btrfs device stats | ✅ |
| Disk I/O utilization (/proc/diskstats) | ✅ |
| Per-process memory tracking + OOM projection | ✅ |
| **ExternalCollector** — services in one 60s poll | ✅ |
| Docker container lifecycle (async stream) | ✅ |
| Tailscale mesh peer monitoring | ✅ |
| Service + port monitoring (Sunshine, SSH, Ollama) | ✅ |
| Security CVE scanning (arch-audit, daily) | ✅ |
| Coredump crash detection | ✅ |
| Orphan packages + .pacnew files | ✅ |
| **BatchAnalyzer** — LLM every 5min, not per-event | ✅ |
| Pattern detection across events (correlation) | ✅ |
| Priority-based journald (no regex, zero maintenance) | ✅ |
| Kernel message bypass (OOM/panic in 0.5s) | ✅ |
| GPU utilization: 5% (was 95% in v0.2) | ✅ |

### Planned

| Feature | Version |
|---|---|
| Chat interface with intent preview | v0.3 |
| Hybrid LLM routing (local → cloud fallback) | v0.4 |
| Embedding-based anomaly detection (sqlite-vec) | v0.4 |
| MCP server (Claude Code integration) | v0.5 |
| Multi-distro support (Fedora, openSUSE) | v0.5 |
| Telegram/iMessage/SMS briefings + remote query | v0.8 |

## Architecture

```
┌──────────────────────────────────────────────────┐
│                    PySide6 UI                     │
│         Tray Icon ← StatusTracker → Popup Feed    │
└─────────────────────┬────────────────────────────┘
                      │ Qt Signals
┌─────────────────────┴────────────────────────────┐
│               AsyncioBridge (QThread)             │
└─────────────────────┬────────────────────────────┘
                      │ asyncio
┌─────────────────────┴────────────────────────────┐
│                   EventBus                        │
│     publish ← Sources    subscribe → DB, Hooks    │
├──────────────────────────────────────────────────┤
│  3 Collectors     │  Consumers       │  Services  │
│  · MetricsCollect │  · DB persist    │  · Ollama  │
│    (GPU,CPU,disk, │  · StatusTracker │  · Sched.  │
│     PSI,hwmon,    │  · HookManager  │  · Trend   │
│     SMART,btrfs)  │  · RingBufferLog│  · Batch   │
│  · ExternalCollect│  · CrashDetect  │    LLM     │
│    (Docker,Tailsc,│                  │  (5min)    │
│     CVE,coredump) │                  │            │
│  · JournaldSource │                  │            │
│    (priority-only)│                  │            │
├──────────────────────────────────────────────────┤
│                 SQLite WAL + Ollama               │
└──────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| UI | PySide6 (Qt6) | Best Wayland/KDE Plasma 6 support |
| LLM | Ollama + Jazari-4B | Private, offline, 1.2s/event, root cause analysis |
| Event bus | asyncio pub/sub | Zero dependency, backpressure |
| Database | SQLite WAL | Embedded, fast, proven |
| File watching | watchdog (inotify) | Event-driven, debounced |
| Log monitoring | python-systemd | Direct journal access |
| Scheduling | asyncio-native | Cron + interval jobs |
| Hook system | pluggy | Extensible plugin architecture |

## Quick Start

```bash
# Requirements: Python 3.12+, Ollama, Linux (Arch-based recommended)

# Clone and install
git clone https://github.com/mahsumaktas/ailm
cd ailm
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Install system dependency (Arch/CachyOS)
sudo pacman -S python-systemd

# Pull an LLM model (Jazari recommended, or any Ollama model)
ollama pull jazari-4b-sft       # 4.5 GB, fastest, 100% JSON
# or: ollama pull qwen3.5:9b   # 6.6 GB, general purpose
# or: ollama pull gpt-oss:20b  # 13 GB, highest quality

# Configure (optional — defaults work out of the box)
mkdir -p ~/.config/ailm
cat > ~/.config/ailm/config.toml << 'EOF'
[llm]
model = "jazari-4b-sft"
EOF

# Run headless
ailm --no-ui

# Or install as systemd user service
cp contrib/ailm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ailm

# Optional: control panel tray (start/stop, model switch)
python contrib/ailm-control.py &
```

## Resource Usage

ailm is designed to be invisible:

| Metric | Value |
|---|---|
| CPU (idle) | ~0.1% (3 collectors, event-driven) |
| RAM | ~79 MB |
| GPU | ~5% idle (batch LLM every 5min) |
| VRAM | ~2.7 GB (Jazari-4B via Ollama) |
| Collectors | 3 active (metrics 30s, external 60s, journald stream) |
| LLM calls | ~12/hour batch (was 252/min per-event) |
| Disk | ~50 MB ringlog + ~5 MB rotating logs |

## Supported Distributions

| Distro | Status | Notes |
|---|---|---|
| CachyOS | ✅ Primary target | snap-pac, snapper, rebuild-detector |
| Arch Linux | ✅ Supported | Same package ecosystem |
| EndeavourOS | ✅ Supported | Same package ecosystem |
| Fedora | 📋 Planned v0.5 | dnf backend |
| openSUSE | 📋 Planned v0.5 | zypper backend |

## Development

```bash
# Run tests (528 passing)
python -m pytest tests/ -q

# Type checking
mypy ailm/

# Linting
ruff check ailm/
```

## Research Context

ailm draws from several research threads:

- **OS-Copilot** (ICLR 2024) — generalist OS agents
- **AIOS** (COLM 2025) — LLM agent OS with memory management
- **LogLLM** (2024) — LLM-based log anomaly detection
- **ReAct** (ICLR 2023) — reasoning and acting in language models

See [docs/research-context.md](docs/research-context.md) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributions welcome.

## License

MIT — see [LICENSE](LICENSE).
