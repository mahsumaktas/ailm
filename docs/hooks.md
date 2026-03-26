# ailm Hook System

## Overview

Hooks are the mechanism by which ailm reacts to system events.
They are defined in `~/.config/ailm/hooks.toml` and processed
by the pluggy-based hook engine.

## Built-in Hook Types

### file_changed

```toml
[hooks.pacnew_created]
enabled = true
watch = ["/etc/**/*.pacnew"]
events = ["create"]
action = "pacnew_diff"
notify = true
urgency = "warning"
```

### service_failed

```toml
[hooks.service_failed]
enabled = true
source = "systemd"
pattern = "entered failed state"
action = "root_cause"
notify = true
urgency = "critical"
```

### disk_alert

```toml
[hooks.disk_alert]
enabled = true
threshold_warning = 80
threshold_critical = 95
action = "suggest_cleanup"
notify = true
```

### log_anomaly

```toml
[hooks.log_anomaly]
enabled = true
sources = ["journalctl"]
pattern = "ERROR|CRITICAL|segfault|OOM"
action = "llm_analyze"
notify = "smart"   # don't repeat same alert
```

## Custom Hooks

Users can define arbitrary hooks pointing to their own scripts:

```toml
[[hooks.custom]]
id = "my_backup_check"
description = "Check if backup ran successfully"
watch_file = "/var/log/backups/last_run.log"
events = ["modify"]
script = "/home/user/scripts/check-backup.sh"
llm_summary = true
notify = true
```

ailm will:
1. Detect the file change
2. Run the user script
3. Send stdout to LLM for summary
4. Add to feed with notification if noteworthy

## inotify Limits

Watch lists are kept minimal by default. If you add many
recursive watches, check your inotify limit:

```bash
cat /proc/sys/fs/inotify/max_user_watches
# Default: 8192 on most systems
# CachyOS may have higher defaults

# Increase if needed:
echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.d/99-ailm.conf
sudo sysctl -p /etc/sysctl.d/99-ailm.conf
```

## CachyOS-Specific Integrations

ailm reads these CachyOS tools directly, no hook config needed:

| Tool | What ailm reads | Action |
|---|---|---|
| snap-pac | Post-transaction hook output | "Snapshot #N taken" event |
| snapper | .snapshots directory changes | Snapshot timeline events |
| rebuild-detector | soname bump files | "N packages need rebuild" alert |
| cachyos-reboot-required | /run/reboot-required | Reboot suggestion |
