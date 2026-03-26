# ailm Vision

> This document describes where ailm could go in 2-5 years.
> These are aspirations, not commitments. Some may never ship.
> Written to think clearly about what this project is really for.

---

## The Core Belief

Every Linux power user spends a non-trivial amount of mental energy
managing their system. Checking logs, reviewing updates, handling
.pacnew files, monitoring disk space, tracking service health.

This is largely invisible labor. It doesn't produce anything.
It just prevents things from breaking.

ailm's bet: **an AI that knows your system as well as you do
can absorb most of this labor.** Not by replacing your judgment,
but by doing the routine work so you don't have to.

---

## The Analogy

Think of ailm as the difference between owning a car and having a
mechanic who lives with you.

You don't check tire pressure every morning. Your mechanic does.
When something needs attention, they tell you — in plain language,
with a recommendation, at the right time.

ailm is that mechanic for your Linux system.

---

## Near Term (2026): Getting the Basics Right

The 12-month goal is simple: make ailm something a person actually
uses every day without thinking about it.

That means:
- Morning briefing takes 30 seconds and contains nothing useless
- One-click actions work reliably and are never destructive
- The system is quiet when nothing matters and clear when it does
- Local LLM inference is fast enough to not feel like waiting

If ailm achieves this for Arch/CachyOS users, that is enough
for version 1.0.

---

## Medium Term (2027): The Learning System

The more interesting problem is personalization at the system level.

Two users can have identical hardware and identical distros but very
different system behaviors — because of what they run, how they
work, what they've customized.

A system monitor that treats all users identically is missing most
of the value. ailm should eventually know:

- Which services *you* care about vs. which you never look at
- Which update categories are safe to auto-apply for *your* setup
- Which anomalies are normal for *your* workload
- When *you* are working vs. available for maintenance

This is not generic AI personalization. It's system-specific
longitudinal learning. The data lives locally. The model updates
locally. No telemetry, no cloud profile.

The technical pieces are available today (embedding-based memory,
preference learning, RAG). The challenge is making them feel natural
rather than complicated.

---

## Long Term (2028+): Speculative

### Federated Pattern Sharing

Individual systems accumulate knowledge about their own patterns.
But some patterns are universal — a specific kernel version causing
GPU issues affects many systems, not just yours.

A privacy-preserving federated system could share pattern vectors
(not raw logs) across ailm users. "17% of CachyOS users with RTX
4000 series saw GPU instability within 48h of this mesa version"
is useful signal that no individual user could compute alone.

This requires trust infrastructure that doesn't exist yet.
It may never be worth building. But it's the right direction.

### Self-Healing Autonomy

Today: ailm notifies → user acts.
Near future: ailm suggests → user confirms → ailm acts.
Far future: ailm acts within defined boundaries, logs everything,
            provides undo.

The autonomy boundary problem is hard. "Restart this service" is
safe. "Merge this .pacnew" might not be. "Update these packages"
depends entirely on context.

The right model: ailm earns autonomy level by level, action by
action, based on demonstrated reliability. Start maximally
conservative. Expand only when the user grants it explicitly.

### Natural Language System Administration

The semantic file system idea from AIOS: what if you could ask
"what config files did I change in the last month?" or "which
services depend on this library?" and get an accurate answer in
plain language — not because an LLM guessed, but because ailm
actually tracked it.

This is not AI replacing sysadmin knowledge. It's AI making
sysadmin knowledge accessible through a better interface.

### Multi-Machine

ailm on a home server, a workstation, a laptop — all talking to
each other. "Your server's disk is at 89%, you might want to
move some of that media library before the weekend."

Cross-machine context that humans currently have to maintain
manually in their heads.

---

## What ailm Will Not Become

To be clear about scope:

- **Not a general AI assistant.** ailm is system-focused.
  For general chat, use Claude / ChatGPT / etc.

- **Not a replacement for human judgment on critical actions.**
  Package managers, system configs, service management — users
  stay in control. ailm informs and suggests. It does not decide.

- **Not a cloud product.** The default is and always will be
  fully local. Cloud LLM is an opt-in feature for users who want
  deeper analysis and accept the tradeoff.

- **Not a monitoring platform for servers.** Prometheus, Grafana,
  Datadog — those are the right tools for infrastructure.
  ailm is for your personal machine.

---

## The Research Gap ailm Fills

Surveying the academic literature (2024-2025):

- **OS-Copilot** does OS-level automation but is reactive (you ask)
- **AIOS** provides kernel abstractions but no tray, no proactivity
- **LogLLM** does log anomaly detection but no UI or personalization
- **IFSHM** does self-healing but for cloud systems, not desktop

The specific combination — proactive, desktop-native, local-first,
personalized over time, building on existing distro tooling rather
than replacing it — does not exist as a project.

That's the gap ailm fills.

---

## A Note on Ambition

ailm started as a weekend project idea. The vision above is bigger
than any one person should attempt.

The right approach: build the small version well. Let users tell
you what actually matters to them. Let the project grow from real
usage, not from speculation.

The vision is here so the early decisions point in the right direction.
It is not a promise. It is a compass.
