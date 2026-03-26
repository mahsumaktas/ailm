<div align="center">
  <h1>ailm</h1>
  <p><strong>AI-powered Linux system companion that watches your machine and tells you what matters.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/status-pre--alpha-orange" />
    <img src="https://img.shields.io/badge/platform-Linux-blue" />
    <img src="https://img.shields.io/badge/LLM-local%20%7C%20cloud-green" />
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" />
  </p>
  <p>
    <a href="README.tr.md">Türkçe</a> ·
    <a href="ROADMAP.md">Roadmap</a> ·
    <a href="VISION.md">Vision</a> ·
    <a href="docs/architecture.md">Architecture</a>
  </p>
</div>

---

## What is ailm?

ailm is a system tray daemon for Linux that monitors your machine, learns your habits,
and gives you a morning briefing — all powered by a local LLM running on your own hardware.

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
│  11:42 ⚠ CopyQ FD: 650/65K     │
│  14:20 ✓ 3 packages updated    │
│  15:01 · .pacnew: pacman.conf   │
│─────────────────────────────────│
│  > Ask something...         [⏎] │
└─────────────────────────────────┘
```

## Why ailm?

Rolling release distributions (Arch, CachyOS, EndeavourOS) are powerful but noisy.
Every day brings updates, .pacnew files, service failures, disk pressure, kernel changes.
Most users either ignore these signals or spend too much time chasing them.

ailm sits in the middle: it reads the noise, understands the context, and surfaces only
what actually matters to you — in plain language.

## Core Philosophy

- **Proactive over reactive.** ailm tells you before you ask.
- **Local by default.** Your logs never leave your machine unless you choose cloud LLM.
- **Listener not doer.** ailm reads existing tools (snapper, pacman, systemd) — it doesn't replace them.
- **Learns over time.** The more you use it, the less noise you see.
- **Graceful degradation.** If Ollama is down, ailm still works — events queue, analysis waits.

## Key Features (Planned)

| Feature | Status |
|---|---|
| System tray icon (green/yellow/red) | 🔨 Building |
| Activity feed with LLM summaries | 🔨 Building |
| Morning briefing (daily digest) | 🔨 Building |
| journald log monitoring + anomaly detection | 🔨 Building |
| Package update tracking (pacman/alpm) | 🔨 Building |
| Snapshot event tracking (snapper/snap-pac) | 🔨 Building |
| Disk pressure alerts with forecast | 🔨 Building |
| Failed service detection | 🔨 Building |
| .pacnew detection + merge suggestions | 📋 Planned |
| File change monitoring (/etc) | 📋 Planned |
| Scheduled tasks (TOML config) | 📋 Planned |
| Hybrid LLM routing (local → cloud fallback) | 📋 Planned |
| User preference learning | 📋 Planned |
| MCP server (Claude Code integration) | 📋 Planned |
| Multi-distro support (Fedora, openSUSE) | 📋 Planned |

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| UI | PySide6 (Qt6) | Best Wayland/KDE Plasma 6 support, 20-40MB RAM |
| System tray | SNI via DBus | Native KDE/GNOME compatibility |
| LLM (local) | Ollama (qwen3.5:9b default) | Fast, private, offline |
| LLM (cloud) | Claude API (optional) | Deep analysis on complex events |
| Event bus | asyncio.Queue + pub/sub | Zero dependency, reliable |
| Database | SQLite WAL | Embedded, fast, proven |
| Vector search | sqlite-vec | Semantic memory, zero infra |
| Embeddings | nomic-embed-text (Ollama) | 274MB, local, accurate |
| File watching | watchdog (inotify) | Pythonic, cross-platform |
| Scheduling | APScheduler v4 | asyncio-native |
| Log monitoring | python-systemd | Direct sd_journal_follow |
| Hook system | pluggy | pytest's hook engine, extensible |

## Installation

> ⚠️ ailm is in pre-alpha. Not yet available via package managers.

```bash
# Requirements: Python 3.11+, Ollama, KDE Plasma 6 (Wayland)
git clone https://github.com/mahsumaktas/ailm
cd ailm
pip install -e ".[dev]"

# Pull default LLM
ollama pull qwen3.5:9b
ollama pull nomic-embed-text

# Run
ailm
```

## Supported Distributions

| Distro | Status | Notes |
|---|---|---|
| CachyOS | ✅ Primary target | snap-pac, snapper, rebuild-detector |
| Arch Linux | ✅ Planned v0.1 | Same package ecosystem |
| EndeavourOS | ✅ Planned v0.1 | Same package ecosystem |
| Fedora | 📋 Planned v0.5 | dnf backend |
| openSUSE | 📋 Planned v0.5 | zypper backend |

## Research Context

ailm draws from several research threads:

- **OS-Copilot** (ICLR 2024) — generalist OS agents with self-improvement
- **AIOS** (COLM 2025) — LLM agent operating system with memory management
- **LogLLM** (arXiv 2024) — LLM-based log anomaly detection
- **IFSHM** (arXiv 2025) — intelligent fault self-healing with LLM+DRL
- **ReAct** (ICLR 2023) — reasoning and acting in language models
- **MemEvolve/MemRL** (2025-2026) — evolving agent memory systems

See [docs/research-context.md](docs/research-context.md) for a full review.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributions welcome.

## License

MIT — see [LICENSE](LICENSE).
