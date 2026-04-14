# ailm Daily Analysis — 2026-04-14

## Summary

Full code quality and architecture review of the ailm v0.3 codebase.
**4 bugs fixed, 0 test regressions** (464 passed / 1 skipped, same as before).

---

## 1. Test Suite

```
python3.12 -m pytest tests/ -q --ignore=tests/test_ui
464 passed, 1 skipped in 6.17s
```

UI tests skipped — PySide6 not installed in this environment.
All non-UI tests green before and after fixes.

---

## 2. Ruff

```
ruff check ailm/
All checks passed!
```

No lint issues found in any file.

---

## 3. Bugs Found and Fixed

### Bug 1 — Subprocess zombie leak (10 locations) — FIXED

**Severity:** HIGH — affects 24/7 stability

**Files:** `ailm/sources/metrics.py`, `ailm/sources/external.py`

**Problem:** When `asyncio.wait_for(p.communicate(), timeout=N)` times out, the
`asyncio.TimeoutError` was caught and the function returned — but the subprocess
was never killed. The process kept running in the background, accumulating as an
orphaned zombie over time. On a system running 24/7, this causes a slow bleed of
unreaped processes.

The NVIDIA check in `metrics.py` already had the correct pattern (`if p: p.kill()`);
none of the other 10 locations did.

**Affected locations:**
- `MetricsCollector._probe`
- `MetricsCollector._smart` (inside loop over NVMe devices)
- `MetricsCollector._btrfs`
- `ExternalCollector._probe`
- `ExternalCollector._tailscale`
- `ExternalCollector._services_ports` (systemctl is-active call)
- `ExternalCollector._coredumps`
- `ExternalCollector._pacnew`
- `ExternalCollector._security`
- `ExternalCollector._orphans`

**Fix:** Split each `except (OSError, asyncio.TimeoutError)` into two branches;
on `TimeoutError` call `p.kill()` before returning:

```python
# Before
try:
    p = await asyncio.create_subprocess_exec(...)
    out, _ = await asyncio.wait_for(p.communicate(), timeout=15)
except (OSError, asyncio.TimeoutError):
    return

# After
p = None
try:
    p = await asyncio.create_subprocess_exec(...)
    out, _ = await asyncio.wait_for(p.communicate(), timeout=15)
except asyncio.TimeoutError:
    if p is not None:
        p.kill()
    return
except OSError:
    return
```

---

### Bug 2 — btrfs/NVMe errors missed when baseline is zero — FIXED

**Severity:** HIGH — monitoring blind spot

**File:** `ailm/sources/metrics.py`

**Problem:** Both `_btrfs()` and `_smart()` used a `prev > 0` guard to avoid false
alarms on the initial baseline read. This had an unintended side effect: if the
baseline on first run was 0 (no errors), and errors appeared on the second run,
the increase was silently dropped.

```python
# _btrfs — BEFORE
if v > prev and prev > 0:   # BUG: v=3, prev=0 → no alert for new errors!

# _smart — BEFORE
if prev_me > 0 and me > prev_me:  # Same bug
```

The correct sentinel for "no baseline yet" is the absence of the key in the
tracking dict, not a zero value.

**Fix:**

```python
# _btrfs — AFTER
if k in self._prev_btrfs and v > prev:

# _smart — AFTER
if dev in self._prev_smart and me > prev_me:
```

This preserves the original intent (no false alarm on first run) while correctly
detecting errors that appear from a zero baseline.

---

### Bug 3 — Disk critical alert silenced forever after warning-range dip — FIXED

**Severity:** MEDIUM — monitoring gap for oscillating disk usage

**File:** `ailm/sources/metrics.py`, `_disk_usage()`

**Problem:** The `"dc"` (disk critical) alert flag was only cleared when disk
usage dropped *below* the warning threshold. If the disk oscillated between the
warning and critical ranges without dropping below warning, `"dc"` stayed `True`
permanently, silencing all future critical alerts.

Scenario: disk → 97% (CRITICAL, dc=True) → 90% (warning range, dc stays True) →
97% again (no alert — dc already True).

```python
# BEFORE
elif pct >= self._disk_warn and not self._alerts.get("dw"):
    ...
elif pct < self._disk_warn:
    self._alerts["dc"] = self._alerts["dw"] = False
# Missing: dc not cleared when in warning range
```

**Fix:** When disk is in the warning range (not critical), always clear `"dc"` so
a subsequent critical breach can re-alert:

```python
# AFTER
elif pct >= self._disk_warn:
    self._alerts["dc"] = False  # allow re-alert if disk goes critical again
    if not self._alerts.get("dw"):
        self._alerts["dw"] = True
        await self.bus.publish(...)
else:
    self._alerts["dc"] = self._alerts["dw"] = False
```

---

### Bug 4 — Dead aggregation code in `EventDedup` — FIXED

**Severity:** LOW — code quality / false complexity signal

**File:** `ailm/core/dedup.py`

**Problem:** The v0.3 comment in `should_publish()` says "Source-level aggregation
disabled in v0.3 — batch LLM handles noise", but the full `_check_source_aggregate`
method (40 lines) and its three supporting state dicts remained in `__init__`:

```python
self._source_counts: dict[str, deque[float]] = {}
self._source_samples: dict[str, list[str]] = {}
self._source_agg_last_emit: dict[str, float] = {}
```

The method was never called from anywhere. This is misleading (readers wonder if
it runs somewhere), allocates three unused dicts, and inflates the class surface.

**Fix:** Removed `_check_source_aggregate` and the three state dicts. The
`DedupConfig.aggregate_threshold` / `aggregate_window_seconds` fields are kept
since they are referenced in tests.

---

## 4. Architecture Observations (no action needed)

### Sources — polling vs event-driven

- **PacmanSource** and **SnapshotSource** are already event-driven (watchdog) — correct.
- **MetricsCollector** (30s poll) and **ExternalCollector** (60s poll): polling is
  appropriate here. The metrics are continuous scalars; inotify would not help.
  Docker events are already streamed via `_docker_stream`.
- **ServiceMonitor** (300s poll): could be driven by systemd D-Bus signals, but
  polling is simpler and 5-minute latency is acceptable for failed-unit detection.
- **RebootSource** (300s poll): kernel version check requires a filesystem stat and
  a `platform.release()` call — polling is the right choice here.

### `PollingSource._loop` — immediate first check

The loop runs `check()` first, then sleeps. All sources fire simultaneously at
startup. This is intentional and fine — the first check establishes baselines
(btrfs, SMART, disk, process list) so subsequent runs can detect deltas correctly.

### TrendTracker — bounded metric set

`_metrics` dict is never pruned. In practice the metric keys are a fixed set
(cpu_pct, ram_pct, disk_pct, etc.) so this is not a real memory leak. If the
set were dynamic (e.g., per-container metrics), pruning would be needed.

### EventBus — subscriber ordering

Subscribers registered with `event_type=None` are dispatched in registration
order: persist → status_tracker → hooks → ringlog. This ordering is correct:
the event is persisted before hooks fire (hooks can query the DB), and ringlog
is last (cheap sync write, non-blocking).

### `health_job` complexity

The health job is already minimal: `status_tracker.prune()` + `llm.health_check()`.
No changes needed.

---

## 5. Files Changed

| File | Change |
|------|--------|
| `ailm/sources/metrics.py` | Kill subprocess on timeout in `_probe`, `_smart`, `_btrfs`; fix SMART/btrfs baseline=0 bug; fix disk alert hysteresis |
| `ailm/sources/external.py` | Kill subprocess on timeout in `_probe`, `_tailscale`, `_services_ports`, `_coredumps`, `_pacnew`, `_security`, `_orphans` |
| `ailm/core/dedup.py` | Remove dead `_check_source_aggregate` method and 3 unused state dicts |

---

## 6. Stability Assessment

The system is well-architected for 24/7 operation:
- All subprocess calls use `create_subprocess_exec` (no shell injection).
- Dedup prevents event floods from journald.
- Ring buffer log survives power loss (fdatasync every 10s).
- Crash detector catches unclean shutdowns on next boot.
- Batch LLM (every 5 min, temperature=0) is appropriate — avoids GPU overload.
- No bare `except:` blocks found.

After today's fixes: no known subprocess zombie sources, no monitoring blind spots
for zero-baseline errors, no silently-stuck alert flags.
