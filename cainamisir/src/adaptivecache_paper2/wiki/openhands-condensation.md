---
title: OpenHands Context Condensation (All-Hands.dev, 2025)
type: source
tags: [context-management, summarization, eviction, open-source, system-design]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# OpenHands Context Condensation

**Organization:** All-Hands.dev  
**Type:** Open-source system (production implementation)  
**Source:** GitHub — `openhands/memory/condenser/`  
**Cited as:** Ref [1] in Context-Folding (Sun et al., 2025)

## Overview

OpenHands is an open-source AI software developer agent. Its context condensation system is a modular, pluggable framework for reducing event history before it exceeds the working context limit. Unlike most academic approaches, this is a real production system with multiple interchangeable strategies.

## Architecture

The system is built around an abstract `Condenser` base class with a registry pattern. Multiple strategies are composed via a `pipeline.py` that chains condensers in sequence. The trigger is threshold-based: a `RollingCondenser` calls `should_condense()` at each step, and if true, runs `get_condensation()`.

Condensation metadata is tracked in the agent `State` via `extra_data['condenser_meta']`, helping the LLM understand what was summarized.

## Implemented Strategies (`impl/`)

| Condenser | Mechanism |
|---|---|
| `llm_summarizing_condenser` | LLM generates abstractive summary of history — the main production strategy |
| `llm_attention_condenser` | LLM identifies which events are most important to preserve |
| `structured_summary_condenser` | LLM produces structured summary with preserved hierarchy |
| `conversation_window_condenser` | Sliding window — keeps recent N events, discards older ones |
| `recent_events_condenser` | Keeps only the most recent events |
| `amortized_forgetting_condenser` | Gradual importance decay — older events lose weight over time |
| `browser_output_condenser` | Filters/compresses verbose browser outputs specifically |
| `observation_masking_condenser` | Masks sensitive observation data |
| `no_op_condenser` | Pass-through — no condensation (used in pipelines as placeholder) |

In practice, the LLM-based strategies (summarizing, attention, structured) are the most capable but most expensive. Window/recency strategies are fast but lossy.

## Key Design Properties

- **Trigger**: threshold-based (context length) — fires post-hoc when the window fills
- **Output**: new summarized text tokens, not a reordering of existing tokens
- **Granularity**: event-level (tool calls, observations) not token-level
- **Pipeline composition**: multiple strategies can be chained (e.g., mask browser output first, then LLM-summarize)
- **Stateful**: condensation metadata is tracked across cycles so the model knows what's been compressed

## Relationship to AdaptiveCache

OpenHands condensation is the **most widely deployed** example of the approach AdaptiveCache is designed to outperform.

| Dimension | OpenHands Condensation | AdaptiveCache |
|---|---|---|
| Trigger | Post-hoc when context fills | Proactive at every step |
| Mechanism | LLM generates new summary text | Reorder/evict existing tokens |
| Prefix KV-cache | Invalidated — new summary tokens have no cached KV state | Preserved — reordering without generation keeps stable prefix |
| Layout-aware | No | Yes |
| Training needed | Optional (LLM-based = no training; RL variants possible) | No |
| Modular/plug-in | Yes (registry pattern) | Yes (designed as drop-in layer) |
| Granularity | Event-level | Token/item level |

**Critical gap**: Every LLM-based condenser in OpenHands generates new tokens for the summary. These tokens have never been seen before, so there is no cached KV state for them. The next step starts from scratch on the summarized prefix. AdaptiveCache avoids this entirely by working within the existing token set.

**Practical note**: OpenHands condensation is cited in Context-Folding as representative of the summarization-based approach. ReSuM (+4.5% training-free) and Context Folding (62% BrowseComp-Plus) both position themselves as improvements over OpenHands-style condensation. AdaptiveCache targets the same baseline.

**One genuine strength**: The pipeline composition system is elegant — browser output can be filtered cheaply before the expensive LLM summarization pass. AdaptiveCache could benefit from a similar selective approach (apply layout optimization only to high-value items, not all tokens uniformly).

## Related Pages

- [context-management.md](context-management.md) — taxonomy (Summarization-Based, §4)
- [resum.md](resum.md) — plug-and-play external summarizer; most direct academic equivalent
- [summarization-rl.md](summarization-rl.md) — RL-trained summarization policy
- [context-folding.md](context-folding.md) — cites OpenHands as the baseline to beat
