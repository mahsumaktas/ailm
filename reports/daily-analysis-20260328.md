# ailm Code Quality & Architecture Review — 2026-03-28

## Executive Summary

497 tests pass. 3 real bugs fixed, 7 linter warnings fixed.
Focus: stability issues that could silently degrade 24/7 operation.

---

## Bugs Fixed

### BUG-1 — Duplicate `disk_usage_pct` tracking biases TrendTracker slope (app.py)

**Severity**: High (silent data corruption — wrong trend alerts or missed alerts)

**Root cause**: `DiskMonitor.check()` already calls
`self._trend.update("disk_usage_pct", pct, ...)` on every poll (default 300 s).
`health_job` was *also* calling `self.trend_tracker.update("disk_usage_pct", ...)` every 30 s.

Because TrendTracker's window is time-stamped, 30 s updates would fill the 60-sample
half-window in ~15 minutes while DiskMonitor's updates appeared as sparse noise.
This over-represented the short-term trend and could trigger (or suppress) slope
alerts inconsistently.

**Fix**: Removed `disk_usage_pct` from `health_job`'s `_TREND_THRESHOLDS` dict and
from the metrics snapshot. DiskMonitor is the sole feeder for this metric.
Removed the now-dead disk time-to-full projection block that only ran when the
(now-absent) alert fired.

**Files**: `ailm/app.py`

---

### BUG-2 — Per-PID TrendTracker keys grow without bound (app.py)

**Severity**: Medium (memory leak in long-running process)

**Root cause**: The health_job built keys as `proc_{name}_{pid}_gb`.
When a process restarts it gets a new PID → new key → old key is never pruned.
`TrendTracker._metrics` has no eviction logic; on a busy machine with frequent
short-lived processes (systemd oneshots, cron jobs) this dict would grow
continuously for the lifetime of the daemon.

**Fix**: Changed key to `proc_{name}_gb` (process-name only, no PID).
The trend-over-time is still meaningful (same process name, same memory concern)
and the dict is now bounded by the number of distinct process names that ever
exceed 500 MB, which is small and stable.

**Files**: `ailm/app.py`

---

### BUG-3 — `_llm_call_times` is a mutable class variable (app.py)

**Severity**: Low (affects tests and any multi-instance scenario)

**Root cause**:
```python
class Application:
    _LLM_MAX_PER_MINUTE = 10
    _llm_call_times: list[float] = []   # shared across ALL instances
```
Mutable class-level list is shared by every `Application` instance.
In the test suite, one test's LLM calls polluted the rate-limiter state for
the next test, making rate-limit tests order-dependent.

**Fix**: Moved `_llm_call_times` initialisation to `Application.__init__`
as a proper instance attribute.

**Files**: `ailm/app.py`

---

## Linter Issues Fixed (ruff)

| File | Rule | Issue |
|------|------|-------|
| `ailm/core/crash.py` | E741 ×3 | Ambiguous variable name `l` in generator exprs (lines 110–112) — renamed to `line` |
| `ailm/core/dedup.py` | F401 | `dataclasses.field` imported but never used |
| `ailm/sources/hwmon.py` | F841 ×2 | `read_iops` and `write_iops` computed but never read |
| `ailm/sources/journald.py` | F401 | `DedupDecision` imported but never used |
| `ailm/sources/orphan.py` | E741 ×2 | Ambiguous variable name `l` — renamed to `line` |
| `ailm/sources/syshealth.py` | E701 ×5 | Multiple statements on one line (`if x: stmt`) — expanded to two lines each |

---

## Architecture Observations (no changes required)

### Polling sources that could be event-driven

| Source | Current | Could be |
|--------|---------|---------|
| `ServiceMonitor` | polls `systemctl --failed` every 300 s | Subscribe to `systemd` D-Bus signals (`UnitActiveStateChanged`) |
| `PacmanSource` | `WatchdogSource` on pacman.log | Already event-driven ✓ |
| `RebootSource` | One-shot on startup | Already correct ✓ |
| `DockerSource` | Streams `docker events` | Already event-driven ✓ |

`ServiceMonitor` is the main polling candidate for event-driven conversion.
The D-Bus approach would give sub-second failure detection vs 5-minute worst-case
lag. Left as-is — the polling approach is simpler and 300 s is acceptable.

### Duplicate psutil reads across health_job and dedicated sources

`health_job` reads `psutil.virtual_memory()`, `psutil.swap_memory()`,
`psutil.net_io_counters()`, and `psutil.cpu_percent()`.
`SysHealthSource` reads `/proc/sys/fs/file-nr`, `/proc/uptime`, `/proc/pressure/*`
(no overlap with psutil).
`HwmonSource` reads sysfs sensor paths (no overlap).
**No redundant psutil reads** between sources — each domain is owned by one component.

### health_job complexity

`health_job` (~90 lines) bundles CPU/RAM/swap/network trend sampling, process
memory scanning, event-frequency tracking, and LLM health-check.
This is acceptable for a private closure but could be refactored into
`_sample_system_metrics()` and `_check_llm_health()` helper coroutines if it
grows further.

### Dedup state machine edge cases

- **First-time noisy source**: `_source_agg_last_emit[source]` is `0.0` → `last_emit > 0` is
  False → aggregate summary emitted immediately. Correct.
- **Rate-limited first occurrence** (`last_emitted = None`): on next tick the event
  is re-tried correctly; `suppressed_count` accurately reflects missed occurrences. Correct.
- **Baseline re-emit**: only the `last_emitted` timestamp is reset, not `first_seen`.
  Long-running noisy units will keep resetting `count = 1` and correctly summarise
  "N suppressed" events. Correct.
- **`_maybe_prune` interval**: prunes at `2 × baseline_seconds`. With default
  `baseline_seconds=300` that is 10 minutes. Under normal journal volume (hundreds
  of distinct units) this is fine. Under extreme journal storms the `_states` dict
  could temporarily hold thousands of entries but will be pruned promptly.

### TrendTracker memory (after BUG-2 fix)

All remaining metric keys are either static (fixed source names like
`"cpu_pct"`, `"ram_pct"`) or bounded-cardinality (sensor paths on a specific
machine). No further unbounded growth path identified.

### RingBufferLog rotation correctness

Rotation is called from within `write()` while holding `self._lock`.
`_rotate()` sets `self._fd = None`, renames the file, then reopens.
During this brief window inside the lock, `sync_now()` would block on `self._lock`
(safe), and the sync thread's `fdatasync` would also block on the lock (safe).
The rotation is correct and race-free.

### Bus subscriber ordering

Subscribers are registered in this order in `Application.start()`:
1. `_persist_event` → DB
2. `status_tracker.on_event` → StatusTracker
3. `_fire_hook_event` → Hooks
4. `_ringlog_event` → RingLog
5. `_classify_log_event` → LLM (LOG_ANOMALY only)

All are registered **before** sources start — no startup events are lost. ✓
Classification (#5) fires a background `asyncio.create_task`, so DB write (#1)
always completes before LLM updates the event's `summary` field.
`update_summary` is called by `_apply_classification` after `event.id` is set
by the DB insert — correct ordering guaranteed. ✓

---

## Test Results

```
497 passed in 5.22s  (tests/test_ui excluded — requires PySide6)
```

## Ruff Results

```
All checks passed!
```
