# AdaptiveCache Wiki — Index

A catalog of all wiki pages. Updated on every ingest.

---

## Overview

| Page | Summary |
|---|---|
| [overview.md](overview.md) | AdaptiveCache project description, milestones, positioning, open questions |

## Sources

| Page | Summary | Source file |
|---|---|---|
| [context-folding.md](context-folding.md) | Context-Folding (ByteDance Seed, Oct 2025) — branch/return mechanism for active context management; FoldGRPO RL training; 62% BrowseComp-Plus, 58% SWE-Bench | `raw/papers/contextFolding.pdf` |
| [react.md](react.md) | ReAct (Yao et al., ICLR 2023) — foundational Thought-Action-Observation agent loop; primary baseline for AdaptiveCache; full context append, no management | `raw/papers/react.pdf` |
| [summarization-rl.md](summarization-rl.md) | SUPO / Lu et al. (2025) — summarization-augmented MDP with GRPO-style RL; +3.2% CodeGym, +14.0% BrowseComp-Plus vs GRPO baseline; periodic context reset invalidates prefix KV-cache | `raw/papers/summarization-rl.pdf` |
| [mem1.md](mem1.md) | MEM1 (Zhou et al., 2025, Singapore-MIT) — constant-memory agent via `<IS>` internal state replaced each turn; PPO-trained; 3.5× memory reduction, 3.7× perf gain on 16-objective tasks; training-free application hurts | `raw/papers/mem1.pdf` |
| [memagent.md](memagent.md) | MemAgent (Yu et al., 2025, ByteDance/Tsinghua) — chunk-by-chunk with 1024-token overwrite memory; Multi-Conv DAPO RL; O(N) complexity; 8K window trained on 32K, extrapolates to 3.5M tokens | `raw/papers/memagent.pdf` |
| [resum.md](resum.md) | ReSuM (Wu et al., 2025, CUHK/Alibaba/HKUST) — plug-and-play external summarizer (ReSumTool-30B); +4.5% training-free, +8.2% with ReSum-GRPO over ReAct; advantage broadcasting for credit assignment | `raw/papers/resum.pdf` |
| [openhands-condensation.md](openhands-condensation.md) | OpenHands context condensation (All-Hands.dev, 2025) — modular condenser system with 9 pluggable strategies (LLM summarizing, attention, window, forgetting, etc.); most widely deployed production example of summarization-based context management | GitHub source |
| [h2o.md](h2o.md) | H2O (Zhang et al., NeurIPS 2023) — heavy-hitter oracle KV eviction; power-law cumulative attention scores; greedy submodular eviction with H2 set + recent window; up to 29% latency reduction, 1.9× throughput; layout gap: no prefix reuse | `raw/papers/h2o.pdf` |
| [streamingllm.md](streamingllm.md) | StreamingLLM (Xiao et al., ICLR 2024) — attention sinks + rolling window enable infinite streaming at 22.2× speedup; initial tokens receive disproportionate attention due to SoftMax normalization; layout gap: no prefix reuse | `raw/papers/streamingllm.pdf` |
| [scissorhands.md](scissorhands.md) | ScissorHands (Liu et al., NeurIPS 2023) — persistence of importance hypothesis (>95% empirical persistence ratio); history window eviction of consistently low-attention tokens; 5× compression with no accuracy drop; layout gap: no prefix reuse | `raw/papers/scissorhands.pdf` |
| [snapkv.md](snapkv.md) | SnapKV (Li et al., 2024) — observation window prompt-phase compression; last Lobs tokens predict which prefix positions matter during generation (>70% hit rate); per-head top-k with 1D max pooling; 3.6× speedup; layout gap: per-query scatter | `raw/papers/snapkv.pdf` |
| [pyramidkv.md](pyramidkv.md) | PyramidKV (Cai et al., 2024) — attention entropy decreases monotonically across layers; allocates larger KV budgets to lower layers; entropy-proportional schedule outperforms uniform budget; uses SnapKV observation window per layer; layout gap: no prefix reuse | `raw/papers/pyramidkv.pdf` |
| [lost-in-middle.md](lost-in-middle.md) | Lost in the Middle (Liu et al., TACL 2023) — U-shaped performance curve across all tested models; 75.8% → 53.8% → 63.2% for GPT-3.5-Turbo at positions 1/9/20 in 20-doc QA; behavioral justification for AdaptiveCache's stable-prefix layout | `raw/papers/lost-in-middle.pdf` |
| [llmlingua.md](llmlingua.md) | LLMLingua & LongLLMLingua (Jiang et al., Microsoft 2023/2024) — coarse-to-fine prompt compression via small model perplexity; 20× compression with minimal EM drop; LongLLMLingua adds contrastive perplexity + document reordering; layout gap: prefix-invalidating output | `raw/papers/llmlingua.pdf`, `raw/papers/longllmlingua.pdf` |
| [infini-transformer.md](infini-transformer.md) | Infini-Transformer (Munkhdalai et al., Google 2024) — compressive memory matrix + local attention + learned gating; 1.6M constant memory; 100% passkey at 1M tokens; ROUGE 18.5 on BookSum; requires architectural modification; layout gap: recurrent state not prefix-cacheable | `raw/papers/infini-transformer.pdf` |
| [anthropic-compaction.md](anthropic-compaction.md) | Anthropic server-side compaction (Beta 2026) — `compact-2026-01-12`; automatically summarizes conversations when input exceeds threshold (default 150K tokens); `pause_after_compaction` hook; prompt caching compatible for system prompt; layout gap: new summary tokens invalidate conversation prefix KV-cache | Anthropic API docs |
| [claude-code-compaction.md](claude-code-compaction.md) | Claude Code compaction (Anthropic, 2025) — 4-stage pipeline: Snip → Microcompact → Context Collapse → Autocompact. **Cached microcompact** (`cache_edits`) surgically removes cached tool results without invalidating the rest of the prefix — the closest existing mechanism to AdaptiveCache's core idea. Forked agent reuses parent prompt cache for summarization. | `raw/claude_code_compaction.md` |

## Concepts

| Page | Summary |
|---|---|
| [prefix-caching.md](prefix-caching.md) | KV-cache reuse via identical prefixes; cost asymmetry between cached and uncached tokens; why layout matters |
| [context-management.md](context-management.md) | Taxonomy of all context management approaches: full context, eviction, summarization, multi-agent, folding, AdaptiveCache |
| [kv-cache-architecture.md](kv-cache-architecture.md) | Non-contiguous KV eviction is feasible via post-rotated keys + hole-leaving (never renumber); PagedEviction and cache_edits as production evidence; two distinct cache mechanisms (eviction vs prefix hit); design implications for AdaptiveCache |
| [importance-scoring.md](importance-scoring.md) | 2D scoring framework (importance × stability); 7 signals from structural priors to Expected Attention to dependency graph centrality; contrastive dedup; sink-aware layout; milestone mapping |
| [system-design.md](system-design.md) | Full system design specification: 6-step pipeline, block model, three-zone layout, scoring pipeline, eviction engine, layout optimizer, three-tier cost model, formal cost equations, integration points, worked example |
| [research-plan.md](research-plan.md) | Three-phase research plan (instrumentation → core system → learned signals); full baseline comparison table (9 systems); key experiments; infrastructure questions |
| [detailed-research-plan.md](detailed-research-plan.md) | Full detailed research plan with per-experiment compute estimates; ~556–726 A100 hours total; 7B for ablations, 72B for validation, $100 API for cache hit measurement; 13-week timeline; dependency graph and cut list |

---

## Source Count by Tag

| Tag | Pages |
|---|---|
| context-management | context-folding, context-management, overview, react, summarization-rl, mem1, memagent, resum, anthropic-compaction, llmlingua, lost-in-middle, infini-transformer |
| prefix-caching | prefix-caching, overview, react, anthropic-compaction |
| kv-cache | prefix-caching, context-folding, react, summarization-rl, mem1, memagent, resum, h2o, streamingllm, scissorhands, snapkv, pyramidkv, infini-transformer |
| eviction | h2o, streamingllm, scissorhands, snapkv, pyramidkv, context-management |
| attention | h2o, streamingllm, scissorhands, snapkv, pyramidkv, lost-in-middle |
| inference-efficiency | h2o, streamingllm, scissorhands, snapkv, pyramidkv, llmlingua, infini-transformer |
| long-context | memagent, mem1, summarization-rl, streamingllm, snapkv, pyramidkv, llmlingua, lost-in-middle, infini-transformer |
| swe-bench | context-folding, overview |
| reinforcement-learning | context-folding, summarization-rl, mem1, memagent, resum |
| summarization | summarization-rl, resum, context-management, anthropic-compaction |
| agent | react, summarization-rl, mem1, memagent, resum, anthropic-compaction |
| positional-bias | lost-in-middle, llmlingua |
| prompt-compression | llmlingua, context-management |
| compressive-memory | infini-transformer |
| streaming | streamingllm, infini-transformer |
| per-layer | pyramidkv |
| plug-and-play | resum |
| baseline | react |

---

## Papers to Ingest (backlog)

From the Context-Folding reference list — high priority for AdaptiveCache:

- [x] Lu et al. (2025) — "Scaling LLM multi-turn RL with end-to-end summarization-based context management" → [summarization-rl.md](summarization-rl.md)
- [x] Zhou et al. (2025) — Mem1: memory + reasoning for efficient long-horizon agents → [mem1.md](mem1.md)
- [x] OpenHands context condensation (All-Hands.dev, 2025) → [openhands-condensation.md](openhands-condensation.md)
- [x] Yao et al. (2022) — ReAct → [react.md](react.md)
- [x] Yu et al. (2025) — MemAgent → [memagent.md](memagent.md)
- [x] Wu et al. (2025) — ReSuM → [resum.md](resum.md)

KV-cache eviction / inference efficiency (Tier 1):

- [x] Zhang et al. (NeurIPS 2023) — H2O: Heavy-Hitter Oracle → [h2o.md](h2o.md)
- [x] Xiao et al. (ICLR 2024) — StreamingLLM → [streamingllm.md](streamingllm.md)
- [x] Liu et al. (NeurIPS 2023) — ScissorHands → [scissorhands.md](scissorhands.md)
- [x] Li et al. (2024) — SnapKV → [snapkv.md](snapkv.md)
- [x] Cai et al. (2024) — PyramidKV → [pyramidkv.md](pyramidkv.md)
- [x] Liu et al. (TACL 2023) — Lost in the Middle → [lost-in-middle.md](lost-in-middle.md)
- [x] Jiang et al. (EMNLP 2023 / 2024) — LLMLingua & LongLLMLingua → [llmlingua.md](llmlingua.md)
- [x] Munkhdalai et al. (Google 2024) — Infini-Transformer → [infini-transformer.md](infini-transformer.md)
- [x] Anthropic compaction API (Beta 2026) → [anthropic-compaction.md](anthropic-compaction.md)
