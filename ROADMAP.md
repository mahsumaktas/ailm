# ailm Roadmap

> Last updated: 2026-03
> Status: Pre-alpha, active development

This document describes the planned development path for ailm.
Milestones are sequential — each version validates before the next begins.

---

## v0.1 — "Morning Briefing" (Target: 3-4 weeks)

**Core value delivered:** Open your computer → green tray icon → click →
read last night's summary → apply suggested action with one click.
Total interaction: 30 seconds/day.

### Components

| Component | Detail |
|---|---|
| System tray | PySide6, green/yellow/red, hover tooltip |
| Popup | Feed (recent events) + system summary (CPU/RAM/Disk) |
| Event store | SQLite WAL |
| journald listener | python-systemd, regex pre-filter + qwen3.5 classification |
| Package event | pacman.log / alpm hook → "what was updated" record |
| Snapshot event | snapper dbus / .snapshots watch → "snapshot taken" record |
| Disk monitoring | 5min interval, threshold alert with forecast |
| Service monitoring | Failed unit detection |
| Morning briefing | 06:00 cron, LLM summary of collected events |
| Reboot notification | cachyos-reboot-required hook listener |
| One-click actions | Whitelisted commands: restart, vacuum, reboot |
| Graceful degradation | Ollama down → events queue, core functions continue |
| Evidence format | Every LLM output tagged with [DATA] → [SOURCE] labels |
| Distro abstraction | PackageManager / SnapshotBackend / InitSystem Protocol interfaces |

### Log Pipeline

```
journald → regex pre-filter (20 keywords) → matched only → qwen3.5:9b → structured JSON
           (ERROR, CRITICAL, OOM, segfault,    (~95% filtered,
            failed, denied)                      ~5% to LLM)
```

### Validation
- 1 week on developer's own machine
- Zero regressions on daily workflow

---

## v0.2 — "Resilience" (Target: +3 weeks)

**Core value delivered:** Event flood eliminated, crash-resilient logging,
trend detection, hot-reload — ailm becomes a reliable daemon.
Inspired by [pi-power-guard](https://github.com/mahsumaktas/pi-power-guard) patterns.

### Phase 1: Event Dedup + Rate Limiting

v0.1 dogfooding revealed 3400+ log_anomaly events/day (Chrome VAAPI errors).
Pi-power-guard's epsilon + baseline pattern solves this.

| Component | Detail |
|---|---|
| EventDedup | Fingerprint-based dedup with baseline interval |
| Message normalization | Strip PIDs, hex, UUIDs → stable fingerprint hash |
| Baseline emit | Even suppressed events emit summary every 300s |
| Rate limiter | Max 20 events/source/minute (configurable) |
| Notification grouping | Same fingerprint within window → count++ |

```
journald → prefilter → dedup+rate_limit → bus.publish
                        ↓ suppressed
                   count++ (silent)
                        ↓ baseline interval
                   "12 occurrences in last 5m" (single event)
```

### Phase 2: EMA Trend Detection

Pi-power-guard's VoltageTracker pattern: EMA + half-window slope detection.
Catches gradual degradation before thresholds are breached.

| Component | Detail |
|---|---|
| TrendTracker | Per-metric EMA with configurable alpha (default 0.1) |
| Slope detection | Half-window slope over 60-sample sliding window |
| Metrics tracked | disk_usage_pct, event_frequency, llm_latency_ms |
| TREND_ALERT event | "Disk rising 2.1%/day — 95% in ~7 days" |
| Cooldown | Min 10min between alerts for same metric |

### Phase 3: RingBufferLog (Crash-Resilient Logging)

Pi-power-guard's RingBufferLog pattern: append-only log with fdatasync.
journald loses up to 5min on crash — ring log loses max 10s.

| Component | Detail |
|---|---|
| RingBufferLog | Append-only, 50K lines, fdatasync every 10s |
| Archive rotation | current.log → archive-{ts}.log, keep 3 |
| Critical sync | CRITICAL events trigger immediate fdatasync |
| Line format | `TIMESTAMP LEVEL SOURCE key=value...` |
| Disk budget | ~40MB max (current + 3 archives) |

### Phase 4: Boot Crash Detection

Pi-power-guard's CrashDetector pattern: state file + previous session analysis.

| Component | Detail |
|---|---|
| State file | `~/.local/share/ailm/last-state` (clean/booted) |
| Crash detect | Start: prev="booted" → crash. Writes "booted" on start, "clean" on stop |
| Pre-crash analysis | Read ring log tail, count CRITICALs, identify last source |
| BOOT_ANALYSIS event | Published on startup if crash detected |

### Phase 5: SIGHUP Config Reload

Pi-power-guard's hot-reload pattern: SIGHUP re-reads config without restart.

| Component | Detail |
|---|---|
| SIGHUP handler | `loop.add_signal_handler(signal.SIGHUP, reload)` |
| Reload scope | LLM model/timeout, source intervals, dedup params |
| Partial failure | If new LLM fails, restore old client |
| Control panel | Sends `kill -HUP` instead of `systemctl restart` |

### Phase 6: .pacnew Detection

Original roadmap item: detect unmerged .pacnew files after pacman updates.

| Component | Detail |
|---|---|
| PacnewSource | PollingSource, hourly `/etc` scan |
| Diff preview | First 50 lines of `diff -u original .pacnew` |
| CONFIG_CHANGE event | WARNING severity, merge recommendation |
| First-run skip | Populate known set silently on first scan |

### New EventTypes

```python
TREND_ALERT = "trend_alert"      # Phase 2
BOOT_ANALYSIS = "boot_analysis"  # Phase 4
CONFIG_CHANGE = "config_change"  # Phase 6
```

### Implementation Order

```
Phase 1 + 2 (parallel) → Phase 3 + 6 (parallel) → Phase 4 + 5 (parallel)
      critical flood fix        crash foundation        crash detect + reload
```

### Validation
- Event rate drops from 3400/day to <100/day with dedup
- Ring log survives `kill -9` (verify with manual test)
- Crash detection fires after simulated crash
- SIGHUP reload changes LLM model without event loss
- 1 week dogfooding on developer machine

---

## v0.3 — "Automation" (Target: +3 weeks)

**Core value delivered:** Set it and forget it. ailm runs scripts,
summarizes output, suggests actions — all on a schedule you define.

### Components

| Component | Detail |
|---|---|
| Scheduled tasks | APScheduler v4, TOML config |
| Custom script support | User script → LLM summary → feed |
| Update advisor | Pre-update risk analysis + post-check |
| Weekly digest | LLM auto-report |
| Autonomy slider | Notify only ↔ Confirm ↔ Automatic |
| DND mode | Quiet hours + fullscreen blocking |

### Autonomy Model

```
Notify only   →  ailm tells you, you decide everything
Confirm       →  ailm prepares action, you approve with one click  ← default
Automatic     →  ailm acts, logs what it did, undo window (30s)
```

All destructive actions (.pacnew merge, service restart, package operations)
require explicit confirmation regardless of autonomy level.

### Validation
- Beta announcement on r/archlinux, target 20+ users

---

## v0.4 — "Intelligence" (Target: +2 weeks)

**Core value delivered:** ailm gets smarter about your specific system
and stops showing you things you don't care about.

### Components

| Component | Detail |
|---|---|
| 3-tier LLM routing | qwen3.5 → gemma3 / gpt-oss → Claude fallback |
| Embedding anomaly | nomic-embed-text + cosine similarity baseline |
| User preference learning | Learn from "ignore" actions |
| Skill cache | Remember past solutions to recurring problems |
| ACH matrix | Competing hypothesis evaluation for complex events |

### LLM Routing Logic

```python
def route_to_llm(task: Task) -> LLMTier:
    if task.complexity == "simple":      # disk alert, known pattern
        return LOCAL_FAST                # qwen3.5:9b, <2s
    elif task.complexity == "medium":    # config change, new error type
        return LOCAL_CAPABLE             # gemma3 / gpt-oss, <8s
    else:                                # causal chain, unknown failure
        return CLOUD                     # Claude API, with user consent
```

### Embedding Anomaly Detection

```python
# Build 7-day baseline of normal log patterns
# New events: embed → compare to baseline cluster
# Distance > threshold → anomaly flag
# Cold start: first 7 days run in low-sensitivity mode
```

### Validation
- User retention metric: 70%+ daily active after 30 days

---

## v0.5 — "Ecosystem" (Target: +4 weeks)

**Core value delivered:** ailm becomes a platform, not just a tool.

### Components

| Component | Detail |
|---|---|
| Plugin system | pluggy — 3rd party hooks, tasks, analyzers |
| MCP server | Claude Code can query ailm events, status, actions |
| Multi-distro | Fedora (dnf), openSUSE (zypper) backends |
| KDE Connect | Push notifications to phone |
| i18n | TR / EN UI |
| Chat interface | Ask ailm about system state via popup or MCP |
| AUR package | `ailm` and `ailm-git` |

### MCP Server Concept

When ailm has an MCP server, Claude Code can:

```
Claude: "What happened on my system while I was coding?"
ailm-mcp: query_events(since="2h ago") → [list of events]
Claude: "Your mesa update completed successfully. Disk is at 78%,
         you should run vacuum soon. NetworkManager restarted twice
         — probably WiFi signal drops."
```

---

## v0.6 — "Multimodal" (Target: 2026 Q3)

**Core value delivered:** ailm can see your screen and GPU.

### Components

| Component | Detail |
|---|---|
| Screen capture | Wayland screenshot → LLaVA / Qwen2-VL analysis |
| GPU monitoring | nvidia-smi / ROCm metrics, thermal analysis |
| Visual error reading | Error dialogs, crash dumps as images |
| GPU-aware scheduling | Don't run heavy analysis when GPU is busy |

---

## v0.7 — "Causal Intelligence" (Target: 2026 Q4)

**Core value delivered:** ailm understands *why* things happen,
not just *what* happened.

### Components

| Component | Detail |
|---|---|
| Causal timeline | Link events to their causes across time |
| Counterfactual reasoning | "If you had updated yesterday, this would have happened" |
| Pattern memory | "Every mesa update causes GPU issues 2 days later for you" |
| External knowledge | Arch BBS, CVE feeds, GitHub issues integration |

### Example Output

```
ailm: "I noticed your GPU driver crashed again. Looking at the last
       6 months, this happens within 48 hours of a mesa update —
       specifically when the kernel is also updated in the same day.
       mesa 25.3 dropped 2 days ago, linux-cachyos was updated
       yesterday. There's a thread on Arch BBS (linked) with 12
       reports. Suggested: wait 4 days before next joint update,
       or pin mesa to 25.2 temporarily. [Pin Mesa] [Open Thread]"
```

This is the feature that does not exist in any current tool.

---

## v0.8 — "Messaging & Remote Access" (Target: 2026 Q4)

**Core value delivered:** Ask ailm anything from anywhere — iMessage,
Telegram, SMS. Get briefings pushed to your phone. Query your system
remotely via chat.

### Components

| Component | Detail |
|---|---|
| Telegram bot | Bidirectional — briefings out, queries in |
| iMessage bridge | Via Mac mini relay (Tailscale mesh) |
| SMS gateway | Optional, via Twilio or similar |
| MCP tunnel | WebSocket tunnel for remote MCP access (inspired by Poke Gate) |
| Push briefings | Morning briefing auto-sent to configured channels |
| Remote query | "What's my disk usage?" → ailm responds via chat |
| Security | End-to-end auth, rate limiting, command whitelist |

### Architecture

```
Phone (iMessage/Telegram/SMS)
    │
    ▼
Message Bridge (Mac mini / cloud relay)
    │
    ▼ WebSocket tunnel
ailm MCP Server (local machine)
    │
    ▼
EventBus / DB / LLM → response
    │
    ▼
Message Bridge → Phone
```

### Interaction Examples

```
You (Telegram): "ailm durum ne?"
ailm: "Sistem saglıklı. Disk %45, 3 paket güncellendi,
       servis hatası yok. Son anomali: 2 saat önce
       Chrome VAAPI hatası (bilinen sorun)."

You (iMessage): "sabah özeti"
ailm: "📋 Bugünün özeti:
       • 12 paket güncellendi (mesa, linux-cachyos dahil)
       • Reboot gerekli — kernel değişti
       • Disk: %62, trend stabil
       • 3 log anomalisi (hepsi Chrome kaynaklı)"

You (SMS): "restart bluetooth"
ailm: "⚠️ bluetooth.service yeniden başlatılsın mı? [EVET/HAYIR]"
You: "EVET"
ailm: "✅ bluetooth.service yeniden başlatıldı."
```

### Privacy & Security

- Messages are relayed, not stored in cloud
- Tailscale mesh for iMessage bridge (no public endpoints)
- Telegram bot token stays local
- Remote commands go through same ActionRegistry whitelist
- Rate limiting: max 10 queries/minute per channel

---

## v1.0 — "Stable" (Target: 2027 Q1)

- Full test suite (unit + integration + e2e)
- Comprehensive documentation
- Stable plugin API
- Performance benchmarks published
- Security audit completed
- Packaged for major distros

---

## Beyond v1.0 — Research Directions

See [VISION.md](VISION.md) for long-term speculative directions.
