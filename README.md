<div align="center">
  <h1>ailm</h1>
  <p><strong>AI-powered Linux system companion that watches your machine and tells you what matters.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/status-v0.2--dev-blue" />
    <img src="https://img.shields.io/badge/platform-Linux-blue" />
    <img src="https://img.shields.io/badge/LLM-local--first-green" />
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" />
    <img src="https://img.shields.io/badge/tests-528%20passing-brightgreen" />
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

### v0.2 — Resilience (NEW)

Inspired by [pi-power-guard](https://github.com/mahsumaktas/pi-power-guard) patterns.

| Feature | Status |
|---|---|
| Event dedup + rate limiting (fingerprint, baseline, 20/min cap) | ✅ |
| EMA trend detection (half-window slope, disk/latency tracking) | ✅ |
| Crash-resilient ring buffer log (fdatasync 10s, survives power loss) | ✅ |
| Boot crash detection (state file + pre-crash log analysis) | ✅ |
| SIGHUP config hot-reload (LLM model, intervals, dedup params) | ✅ |
| .pacnew detection (hourly /etc scan, diff preview, merge warning) | ✅ |

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
│  Sources          │  Consumers       │  Services  │
│  · DiskMonitor    │  · DB persist    │  · Ollama  │
│  · ServiceMonitor │  · StatusTracker │  · Sched.  │
│  · PacmanSource   │  · HookManager  │  · Actions  │
│  · SnapshotSource │  · LLM classify │  · Dedup   │
│  · RebootSource   │  · RingBufferLog│  · Trend   │
│  · JournaldSource │  · CrashDetect  │            │
│  · PacnewSource   │                  │            │
├──────────────────────────────────────────────────┤
│                 SQLite WAL + Ollama               │
└──────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| UI | PySide6 (Qt6) | Best Wayland/KDE Plasma 6 support |
| LLM | Ollama (local) | Private, offline, configurable model |
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

# Pull an LLM model
ollama pull qwen3.5:9b

# Configure (optional — defaults work out of the box)
mkdir -p ~/.config/ailm
cat > ~/.config/ailm/config.toml << 'EOF'
[llm]
model = "qwen3.5:9b"
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
| CPU (idle) | ~0% (event-driven, not polling) |
| RAM | ~35-40 MB |
| Disk checks | Every 60s (configurable) |
| LLM calls | Only for classification + daily briefing |
| VRAM | 0 (model loaded by Ollama on demand) |

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
