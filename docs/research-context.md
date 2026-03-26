# Research Context

ailm was designed with awareness of the current academic landscape.
This document situates ailm relative to relevant research.

## Related Work

### OS-Copilot (ICLR 2024)
**Paper:** Wu et al., "OS-Copilot: Towards Generalist Computer Agents with Self-Improvement"
**What it does:** Framework for generalist OS agents (Linux/macOS) with self-improvement.
Builds FRIDAY, which outperforms prior methods by 35% on GAIA benchmark.
**Difference from ailm:** OS-Copilot is reactive (user asks, agent acts).
ailm is proactive (system watches, agent initiates). Different interaction model.

### AIOS (COLM 2025)
**Paper:** Mei et al., "AIOS: LLM Agent Operating System"
**What it does:** LLM-as-kernel architecture with scheduling, memory management,
and access control. Achieves 2.1x faster agent execution.
**Difference from ailm:** AIOS is infrastructure for agent developers.
ailm is an end-user product for Linux desktop users.
Lessons applied: memory tier architecture, context management patterns.

### LogLLM (arXiv 2024)
**Paper:** Guan et al., "LogLLM: Log-based Anomaly Detection Using Large Language Models"
**What it does:** BERT for semantic log embeddings + Llama for anomaly classification.
Handles unstable logs without requiring parsers.
**Applied in ailm:** The pre-filter + LLM pipeline design.
The embedding-based anomaly detection in v0.4.

### IFSHM (arXiv 2025)
**Paper:** "Intelligent Fault Self-Healing Mechanism for Cloud AI Systems"
**What it does:** LLM semantic fault interpretation + DRL recovery strategy optimization.
Links heterogeneous log signals to unified root causes.
**Applied in ailm:** Root cause analysis framing.
The insight that "CPU high" / "GC failed" / "timeout" may share one cause.

### ReAct (ICLR 2023)
**Paper:** Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models"
**What it does:** Interleaved reasoning traces and actions. Significantly improves
agent reliability and interpretability.
**Applied in ailm:** The chat interface reasoning model (v0.2+).

### Agent Memory Survey (2025-2026)
**Repo:** Shichun-Liu/Agent-Memory-Paper-List
**Covers:** MemRL, MemEvolve, episodic/semantic/procedural memory systems.
**Applied in ailm:** Three-tier memory architecture (episodic events,
semantic preferences, procedural skills).

### LiteLADR (Springer 2026)
**Paper:** "Efficient System Log Analysis via Quantized On-Device Anomaly Detection"
**What it does:** On-device quantized log anomaly detection with F1 > 98%.
**Applied in ailm:** Validates the feasibility of running log anomaly
detection locally without cloud infrastructure.

## What ailm Does That Others Don't

The specific combination that does not exist in any current tool or paper:

1. **Proactive** — system-initiated, not user-initiated
2. **Desktop-native** — tray icon, notifications, KDE integration
3. **Local-first** — full functionality without internet
4. **Personalized over time** — learns your specific patterns
5. **Builds on existing tooling** — reads snapper, pacman, rebuild-detector
   instead of reimplementing them
6. **Graceful degradation** — works without LLM for core functions

## Gaps ailm Could Fill in the Literature

If ailm reaches v0.7 (causal intelligence), it would represent
a novel contribution:

- **Temporal causal reasoning on personal system history**
  No published work addresses causal chain reconstruction
  on individual desktop system timelines at this granularity.

- **Longitudinal personalization without cloud telemetry**
  Most personalization research assumes cloud infrastructure.
  ailm's local-only preference learning is architecturally distinct.
