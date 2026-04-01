# ailm Daily Analysis — 2026-04-01

## Environment Note

Python 3.12 is required but only Python 3.11 is available in this environment.
Tests (`pytest`) and linter (`ruff`) could not be executed.
All findings are from **static code review only**.

---

## Bugs Found and Fixed

### BUG-1 · GPU temperature alert fires every 30s (EVENT FLOOD) — FIXED

**File:** `ailm/sources/metrics.py` — `_nvidia()` method  
**Severity:** HIGH — stability / sustainability

**Problem:**  
The VRAM alert uses `self._alerts.get("gv")` to prevent re-alerting, but the GPU
temperature check had no equivalent guard:

```python
# BEFORE — fires CRITICAL every 30s while GPU ≥ 85°C
if temp >= 85:
    await self.bus.publish(SystemEvent(...))
```

A GPU sustaining 86°C would emit **2,880 CRITICAL events per day**, saturating the
event bus queue (maxsize=1000), the SQLite DB, and potentially triggering the batch
LLM on a flood of identical events.

**Fix:**
```python
if temp >= 85 and not self._alerts.get("gt"):
    self._alerts["gt"] = True
    await self.bus.publish(...)
elif temp < 80:
    self._alerts["gt"] = False
```

5°C hysteresis band (alert at ≥85°C, clear below 80°C) matches the existing VRAM
alert pattern.

---

### BUG-2 · Large-process memory alert fires every 30s (EVENT FLOOD) — FIXED

**File:** `ailm/sources/metrics.py` — `_processes()` method  
**Severity:** HIGH — stability / sustainability

**Problem:**  
The top-5 process check had no "already alerted" guard:

```python
# BEFORE — emits WARNING every 30s per process over 10GB
if gb > 10:
    await self.bus.publish(SystemEvent(...))
```

A browser or ML workload routinely uses >10GB RAM. This would generate ~2,880
WARNING events/day per process, which both floods the DB and sends spurious work
to the batch LLM every 5 minutes.

**Fix:**  
Added per-process `_alerts[f"proc_{name}"]` tracking with clear-on-drop logic:

```python
alerted_now: set[str] = set()
for pid, name, rss in procs[:5]:
    ...
    if gb > 10:
        alerted_now.add(name)
        if not self._alerts.get(f"proc_{name}"):
            self._alerts[f"proc_{name}"] = True
            await self.bus.publish(...)
# Clear when process drops below threshold
for key in [k for k in self._alerts if k.startswith("proc_") and k[5:] not in alerted_now]:
    self._alerts[key] = False
```

---

### BUG-3 · `_known_pacnew` initialization uses empty set — misses new files — FIXED

**File:** `ailm/sources/external.py` — `_pacnew()` method  
**Severity:** MEDIUM — silent missed detection

**Problem:**  
`_known_pacnew` was initialized to `set()` (empty set). The "first run" guard used
`if not self._known_pacnew:`, which is falsy for an **empty set**:

```python
self._known_pacnew: set[str] = set()   # falsy when empty
...
if not self._known_pacnew:             # True when set is empty!
    self._known_pacnew = cur
    return
```

Scenario:
1. First hourly check — no `.pacnew` files exist. `cur = set()`. Guard is True →
   save empty set, return.
2. New `.pacnew` files appear.
3. Second check — `not self._known_pacnew` is **still True** (set is still empty
   from step 1) → saves new state without alerting. Files silently missed for
   another hour.

**Fix:**  
Use `None` as the sentinel for "never initialized":

```python
self._known_pacnew: set[str] | None = None
...
if self._known_pacnew is None:
    self._known_pacnew = cur
    return
```

This matches the pattern already used correctly in `_orphan_count: int | None = None`.

---

### BUG-4 · Dead `was` variable in `health_job` — FIXED

**File:** `ailm/app.py` — `health_job()` closure  
**Severity:** LOW — dead code

**Problem:**  
`was = self.llm.available` was assigned but never referenced. Ruff would flag this
as `F841 local variable 'was' is assigned to but never used`.

**Fix:** Removed the unused assignment.

---

## Architecture Review

### sources/ — Overall Assessment: GOOD

| Source | Poll vs Event? | Notes |
|--------|---------------|-------|
| `MetricsCollector` | Poll (30s) | Correct — sysfs/psutil not inotify-able |
| `ExternalCollector` | Poll (60s) | Correct — tailscale/ports need active probing |
| `ServiceMonitor` | Poll (300s) | Correct — systemd doesn't expose watch API |
| `PacmanSource` | **Event-driven** (inotify) | Correct — log file append is inotify-able |
| `SnapshotSource` | **Event-driven** (inotify) | Correct — directory creation is inotify-able |
| `JournaldSource` | **Event-driven** (journal.wait) | Correct — journal has native wait API |
| `RebootSource` | Poll (300s) | Correct — kernel module dir doesn't have watch |

No sources are wrongly polling when they could be event-driven.

### sources/ — Minor Observations (not bugs)

**`external.py`: `_docker_stream` stores `self._bus = bus` before `super().start(bus)`**  
Both set `_bus` to the same value. The explicit pre-assignment was needed to make
`self.bus` available before `super().start()` wires the polling loop. Harmless
redundancy, correct intent.

**`external.py`: `arch-audit` probe uses `--help` flag**  
`await self._probe("arch-audit", "--help")` exits with code 0 on Arch. This works
but `arch-audit` with no args would be more conventional. No functional impact.

**`metrics.py`: `_smart()` only alerts on errors AFTER a non-zero baseline**  
`if prev_me > 0 and me > prev_me` — intentional: avoids false-alarming on
historical errors present at first boot. Correct behavior, just worth noting.

### core/ — Overall Assessment: GOOD

**`dedup.py`:** State machine logic is correct. The `_check_source_aggregate` method
and its supporting state (`_source_counts`, `_source_samples`, `_source_agg_last_emit`)
are dead code — the v0.3 comment confirms aggregation was disabled in favor of batch
LLM. These structures never grow because the method is never called; not a leak.
If aggregation is re-enabled in v0.4, the `_maybe_prune()` method would need to
also prune `_source_counts` and `_source_agg_last_emit`.

**`trend.py`:** `_metrics` dict grows per unique metric name. The per-process entries
(`proc_{name}_gb`) accumulate as process names cycle. In practice: ~50–100 unique
names × ~800 bytes per `_MetricState` (deque of 60 tuples) ≈ 40–80 KB. Negligible
but worth noting for long uptimes with many distinct short-lived processes.

**`ringlog.py`:** Rotation is fully protected by `_lock` (held throughout `write()` →
`_rotate()`). No race condition. `read_tail()` opens a new file descriptor — immune
to rotation — acceptable for crash analysis use.

**`crash.py`:** Atomic write via `tempfile.mkstemp` + `fsync` + `rename` is correct.
`on_start()` correctly writes "booted" before analyzing the previous crash, so a
crash during analysis is detected next boot.

### app.py — Overall Assessment: GOOD

**`health_job` complexity:** Simple after the BUG-4 fix — two operations (prune +
LLM health check). Appropriate for a 30s interval job.

**LLM rate limiting:** The batch analyzer runs every 5 minutes and processes at most
50 events per batch. `generate()` returns `None` when `_available=False`, propagating
graceful degradation. The `health_check()` pings every 30s. No correctness issues.

**Event bus subscriber ordering:** Subscribers are registered in this order:
1. `_persist_event` — DB insert
2. `status_tracker.on_event` — status recompute
3. `_fire_hook_event` — user hooks
4. `_ringlog_event` — crash log

This is the correct order: persist before hooks (hooks can read DB), ring log last
(crash analysis benefit from seeing persisted data first).

**Bus queue overflow handling:** `put_nowait` silently drops on `QueueFull`. This is
the right choice for a monitoring system — dropping an event is better than blocking
a source. The maxsize=1000 is generous for the expected event rate.

---

## Stability / Sustainability Summary

| Concern | Status |
|---------|--------|
| GPU overload (LLM) | OK — batch every 5min, ≤50 events, `available` guard |
| Event floods from sources | **FIXED** — BUG-1 (GPU temp) + BUG-2 (proc RAM) |
| Memory growth | OK — dedup prunes every 10min, trend bounded per-metric |
| Missed alerts | **FIXED** — BUG-3 (pacnew initialization) |
| Bus backpressure | OK — drop on full, no blocking |
| Crash recovery | OK — atomic state file, ring log |
| DB growth | OK — daily cleanup at 03:00, retention_days configurable |

---

## Commit

All four fixes were applied. Recommend running the full test suite after upgrading
to Python 3.12:

```bash
cd /home/mahsum/ailm && .venv/bin/python -m pytest tests/ -q
```
