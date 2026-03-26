# ailm Memory System

## Overview

ailm uses a three-tier memory architecture. No tier requires
the LLM's context window to hold all historical data.
Only relevant fragments are retrieved per request.

```
┌─────────────────────────────────────────────────────┐
│                LONG-TERM STORAGE                     │
│  (SQLite + sqlite-vec, unlimited, persistent)        │
│                                                      │
│  Episodic   │  Semantic    │  Procedural             │
│  (events)   │  (prefs)     │  (skills)               │
└──────────────────┬──────────────────────────────────┘
                   │  Retrieval (top-3 relevant)
                   ▼
┌─────────────────────────────────────────────────────┐
│                  LLM CONTEXT WINDOW                  │
│  (~1300 tokens, ephemeral)                           │
│                                                      │
│  System prompt:  300 tokens                          │
│  User profile:   200 tokens  ← from Semantic         │
│  Past context:   400 tokens  ← from Episodic         │
│  Current event:  300 tokens                          │
│  Output format:  100 tokens                          │
└─────────────────────────────────────────────────────┘
```

## Tier 1: Episodic Memory (Events)

What happened, when, and what was done about it.

```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    type        TEXT NOT NULL,    -- 'package_update', 'service_fail', etc.
    summary     TEXT,             -- LLM-generated 1-sentence summary
    raw_data    TEXT,             -- original log/data (not sent to LLM directly)
    user_action TEXT,             -- 'ignored', 'restarted', 'postponed', 'applied'
    embedding   BLOB              -- nomic-embed-text vector
);

-- Retention: 30 days normal, 1 year critical events
-- Critical = user_action = 'applied' OR type = 'service_fail'
```

**Retrieval:** When a new event arrives, embed it and find
top-3 most similar past events. Include their summaries and
user_action in the LLM context.

## Tier 2: Semantic Memory (Preferences)

What the user cares about, learned from behavior.

```sql
CREATE TABLE preferences (
    pattern       TEXT UNIQUE,      -- 'copyq_fd_leak', 'mesa_update'
    learned_pref  TEXT,             -- 'user_postpones', 'user_ignores', 'user_applies'
    confidence    REAL DEFAULT 0.0, -- 0.0 to 1.0
    sample_count  INTEGER DEFAULT 0,
    last_updated  TEXT
);
```

**Learning:** After each user action on an event, update the
preference for that event's pattern.

```python
def update_preference(event_type: str, action: str):
    # If user ignores CopyQ FD alerts 4 times, confidence = 0.67
    # At confidence > 0.8, change how the alert is presented
    # "You've postponed this 5 times. Worth addressing?"
```

## Tier 3: Procedural Memory (Skills)

How to solve known problems.

```sql
CREATE TABLE skills (
    trigger       TEXT UNIQUE,   -- 'pacnew:/etc/pacman.conf'
    solution      TEXT,          -- step-by-step what worked
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_used     TEXT
);
```

**Usage:** Before generating a suggestion for a known problem,
check if ailm has solved it before. Include the past solution
in context: "Last time this happened, you ran X and it worked."

## Context Budget

For a typical morning briefing on qwen3.5:9b (32k context):

| Slot | Content | Tokens |
|---|---|---|
| System prompt | Role, security rules, output format | 300 |
| User profile | Top preferences + learned patterns | 200 |
| Past context | Top-3 similar events from last week | 400 |
| Today's events | 24h summaries (avg 20 tok each, max 50) | 300 |
| **Total** | | **~1200** |

This is 3.75% of the 32k context window.
The remaining 96% is available for LLM reasoning.

## Cold Start

For the first 7 days:
- No preferences learned yet → skip preference injection
- No past events → skip episodic retrieval
- Embedding baseline not established → disable anomaly detection
- Run in "observation mode": high recall, don't suppress anything

After 7 days, the system has enough signal to start personalizing.
