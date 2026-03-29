<div align="center">
  <h1>ailm</h1>
  <p><strong>AI-powered Linux system companion that watches your machine and tells you what matters.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/version-0.3-blue" />
    <img src="https://img.shields.io/badge/GPU_load-5%25-brightgreen" />
    <img src="https://img.shields.io/badge/RAM-79_MB-brightgreen" />
    <img src="https://img.shields.io/badge/LLM-Jazari_4B-green" />
    <img src="https://img.shields.io/badge/license-MIT-lightgrey" />
  </p>
  <p>
    <a href="ROADMAP.md">Roadmap</a> ·
    <a href="VISION.md">Vision</a> ·
    <a href="docs/architecture.md">Architecture</a>
  </p>
</div>

---

## What is ailm?

A lightweight daemon that continuously monitors your Linux machine — hardware sensors, system logs, containers, network peers, security vulnerabilities — and periodically asks a local LLM to summarize what happened and what needs attention.

```
You sleep → ailm watches
You wake up → ailm tells you:
  "GPU VRAM hit 93% overnight (Ollama model too large).
   4 Python processes leaked 12GB RAM each → OOM killed ghostty.
   16 CVEs detected (pam, libxml2 — update recommended).
   Tailscale: MacBook Pro went offline 14 times (WiFi sleep)."
```

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                    3 Collectors                       │
│                                                      │
│  MetricsCollector (30s)    ExternalCollector (60s)   │
│  · CPU, RAM, swap, disk   · Docker containers       │
│  · NVIDIA GPU metrics     · Tailscale peers          │
│  · All hwmon sensors      · Service/port health      │
│  · PSI pressure           · CVE scan (daily)         │
│  · NVMe SMART             · Coredumps                │
│  · Btrfs health           · Orphan packages          │
│  · Disk I/O               · .pacnew files            │
│  · Process memory         ·                          │
│                                                      │
│  JournaldSource (stream)                             │
│  · Priority 0-3 only (ERR+)                          │
│  · Kernel messages always                            │
│  · OOM/panic flush in 0.5s                           │
├──────────────────────────────────────────────────────┤
│                    EventBus                           │
│            publish → DB + RingLog + Hooks             │
├──────────────────────────────────────────────────────┤
│                 BatchAnalyzer (5min)                  │
│  · Queries unanalyzed events from DB                 │
│  · Single LLM call per batch (not per event)         │
│  · Pattern detection + action suggestions            │
│  · GPU duty: ~30s every 5min = <10%                  │
├──────────────────────────────────────────────────────┤
│              SQLite WAL · Ollama · Scheduler          │
└──────────────────────────────────────────────────────┘
```

## Why Not Just Use btop + journalctl?

You can. They show what's happening **right now**. ailm shows what happened **while you were gone**, why it happened, and what you should do about it.

| | btop/journalctl | ailm |
|---|---|---|
| Real-time view | Yes | No (not the goal) |
| Historical timeline | No | Yes (SQLite DB) |
| Cross-event correlation | No | Yes (batch LLM) |
| "Why did this happen?" | No | Yes (root cause) |
| "What should I do?" | No | Yes (action field) |
| OOM forensics | Manual | Automatic |
| Runs while you sleep | No | Yes (systemd service) |
| Resource cost | 0 | 79 MB RAM, 5% GPU |

## Quick Start

```bash
git clone https://github.com/mahsumaktas/ailm
cd ailm
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# System dependency (Arch/CachyOS)
sudo pacman -S python-systemd

# LLM model
ollama pull jazari-4b-sft    # recommended: fast, 2.7 GB VRAM

# Run
ailm --no-ui

# Or as systemd service
cp contrib/ailm.service ~/.config/systemd/user/
systemctl --user enable --now ailm
```

## What It Monitors

Everything. In two poll cycles:

**MetricsCollector (every 30s):**
CPU, RAM, swap, disk capacity, network throughput, PSI pressure (cpu/memory/io), all hwmon temperatures (CPU, VRM, chipset, RAM DIMMs, NVMe, WiFi), all hwmon voltages, fan speeds, NVIDIA GPU (temp, VRAM, power), disk I/O utilization, per-process memory (>500MB), Btrfs device stats (every 300s), NVMe SMART health (every 3600s).

**ExternalCollector (every 60s):**
Docker container lifecycle (async stream), Tailscale peer status, Sunshine + SSH + Ollama port health, coredump detection, orphan packages (daily), .pacnew files (hourly), arch-audit CVE scan (daily).

**JournaldSource (stream):**
All priority 0-3 (EMERG/ALERT/CRIT/ERR) messages. Kernel transport always. OOM/panic/Xid flushed in 0.5s.

## Resource Usage

| Metric | Value |
|---|---|
| CPU | ~0.1% idle |
| RAM | 79 MB |
| GPU | 5% (batch LLM every 5min) |
| VRAM | 2.7 GB (Jazari-4B) |
| LLM calls | ~12/hour |
| Disk | ~55 MB (DB + ringlog) |

## LLM

ailm uses [Jazari-4B-SFT](https://huggingface.co/mahsum/jazari-4b-sft-tr) by default — a Turkish-adapted Qwen3.5-4B model fine-tuned for system log classification. Any Ollama model works.

| Model | VRAM | Speed | JSON reliability |
|---|---|---|---|
| jazari-4b-sft | 2.7 GB | 1.2s/event | 100% (70/70) |
| qwen3.5:9b | 6.6 GB | 3s/event | ~90% |
| gpt-oss:20b | 13 GB | 13s/event | 100% |

## Version History

| Version | Focus |
|---|---|
| v0.1 | Morning briefing, event sources, tray UI |
| v0.2 | Dedup, trend detection, crash-resilient logging |
| **v0.3** | **Radical simplification: 20 sources → 3 collectors, per-event LLM → batch** |

## Development

```bash
python -m pytest tests/ -q    # 482 passing
ruff check ailm/               # zero warnings
```

## License

MIT
