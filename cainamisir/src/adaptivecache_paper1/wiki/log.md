# AdaptiveCache Wiki — Activity Log

Append-only. Format: `## [YYYY-MM-DD] <operation> | <title>`

---

## [2026-04-29] experiment | Phase E v1 outline + retail; Paper 1 reframe

Phase E v1 outline run on SWE-bench Lite (Haiku N=10): plain `consumption_evict` 5/10 real-resolved, `_facts` 4/10, `_outline` 5/10, `none` 5/10. **`pytest-7490` mechanism replicates across seeds**: facts uniquely fails (18 edit-revert iterations on the same `pytest_runtest_call` function — anchoring loop); plain wins with 4 edits; outline wins with 3 edits. **No compaction policy Pareto-beats `none` on cost.** Phase E v2 retail (10 tasks × 4 policies): 0 compactions across all policies (max prompt averaged 9K tokens, never hit the 35K trigger); 9/10 vs 10/10 differences are temperature sampling noise, not policy effect. Confirmed memory note: τ-bench at single-customer scale is too small for compaction. Hit Gate B from PAPER1_PLAN → reframed paper to honest negative result, working title *The Cliff Tax: Why Training-Free Heuristic Compaction Loses to No-Compaction on Strong Agents*, with the placeholder-design ablation as the positive nugget. Real-test validator infrastructure (`scripts/validate_with_tests.py`) replaces the line-overlap oracle. Outputs: `studies/lifetime_cost/out/phase_e_outline_10tasks/`, `phase_e_taubench_retail/`, full mechanism writeup in `studies/lifetime_cost/reports/FINDINGS.md`.

## [2026-04-28] experiment | Phase C SWE-bench Lite live agent — heuristic compaction does not beat `none`

Ran 24 trajectories: 6 policies (`none`, `position_aware`, `prefix_preserving`, `microcompact`, `evict_oldest`, `llm_reorganizer`) × 4 SWE-bench Lite instances (2 `psf/requests`, 2 `pallets/flask`) on Qwen3-30B-A3B-Instruct-2507 via vLLM 0.10.2 V0 engine on RTX PRO 6000 Blackwell. Headline: **none of the compaction policies dominates `none`**. `none` resolves 2/4 (50%); all 5 compaction policies cluster at 1/4 (25%). Per-task: compaction *prevents* context overflow on `psf__requests-3362` (which `none` overflows at 55K), but the dropped tool obs forces extra steps, hitting `max_steps=40` before resolution. On `psf__requests-2317` (which `none` solves cleanly in 26 steps), every compaction policy fails at the 40-step cap. Honest negative result. Full report: `studies/lifetime_cost/reports/PHASE_C_REPORT.md`. Highest-leverage tweak for next run: bump `max_steps` to 80 so compaction has room to recover from re-fetches; bump `max_model_len` to 80K so `none` doesn't overflow on the long tasks. Code/runner changes shipped: `swebench_live.py` benchmark (no-docker SWE-bench Lite via git-diff oracle), `evict_oldest` policy (pure eviction, hole-leaving), `llm_reorganizer` policy (Paper 1's headline claim — small-LLM scoring + drop-bottom-K), `position_aware` fix to use `[evicted]` placeholders instead of deleting messages, runner kick-fix for plain-text turns in tool-only envs (SWE-bench analog of the τ-bench Qwen3 plain-text gotcha), vLLM context-overflow graceful-degradation handler.

## [2026-04-28] experiment | Phase B τ-bench airline smoke — compaction has nothing to compact

Validated A6000 + vLLM serving stack and ran 4 successive Phase B smokes (3 → 9 → 9 → 24 trajectories) on τ-bench airline with `none / position_aware / prefix_preserving / microcompact` policies. Headline: τ-bench airline single-customer tasks fit comfortably in 16K, the prefix cache is naturally ~95% hit-rate, tool obs cap at ~950 tokens — there is nothing for compaction to compact, and policies that fire (prefix_preserving, position_aware) lose to `none` on $/resolved (5.9× more expensive at qwen self-host pricing). Microcompact at conservative threshold=1500 never fired and tied `none`. Conclusion: τ-bench airline is the *wrong benchmark* for the Paper 1 Pareto figure. Need long-context coding tasks. Full Phase B artifacts: `studies/lifetime_cost/out/phase_b_taubench_a6000/`. Required runner patches discovered: (1) Qwen3 emits user-facing replies as plain text instead of `<tool_call>{respond}</tool_call>` — runner needs `respond`-fallback patch or trajectories are 1 step long; (2) Compaction policies that delete messages mid-conversation leave dangling `tool_call_id` refs — must use `[evicted]` placeholder via hole-leaving; (3) `prefix_preserving` rapid-fire pattern on tight budgets — added `cooldown_steps` parameter.

---

## [2026-04-05] synthesis | System Design Specification

Created system-design.md — the authoritative technical specification for AdaptiveCache. 13 sections: (1) three design axioms (prefix stability, joint layout+eviction, no new tokens), (2) 6-step architecture pipeline (block segmentation → scoring → zone assignment → eviction → layout optimization → prompt construction), (3) block model with structural taxonomy and dataclass, (4) three-zone layout (pinned prefix / middle / volatile suffix) with exact placement rules and never-renumber invariant, (5) 2D scoring pipeline with 7 signals mapped to milestones, (6) hole-leaving eviction engine with budget thresholds, (7) layout optimizer with prefix stability constraint, (8) three-tier cost model (hole eviction → layout reorg → emergency summarization), (9) formal token cost equations with break-even analysis (~3 steps at 10× cost ratio), (10) integration points for ReAct/OpenHands/API with attention access modes, (11) 3-step SWE-bench worked example with ASCII diagrams, (12) 8 open design questions mapped to experiments, (13) literature grounding table mapping 11 decisions to source papers. Updated cross-references in: index.md, overview.md, importance-scoring.md, kv-cache-architecture.md, prefix-caching.md.

## [2026-04-05] synthesis | Midterm Checkpoint Document

Drafted midterm.md for CS2640 midterm checkpoint (due 2026-04-06). Structured as: sharpened thesis (the layout gap), refined two-mechanism design (layout optimizer + hole eviction engine), full related work table (10 systems), 5 key challenges, evaluation plan with 9 experiments across 3 phases, ~89 A100 hour compute budget table, risk analysis, preliminary results section (harness in progress). Built on proposal.txt + all wiki content.

## [2026-04-05] synthesis | Detailed Research Plan with Compute Budget

Created detailed-research-plan.md. Full per-experiment breakdown across 3 phases + API validation. Compute estimates: Phase 1 (74 A100 hours, 7B baseline + FIFO/LRU/FixedWindow on 300 instances), Phase 2 (187 A100 hours, layout isolation + signal ablation + full comparison), Phase 3 scoped (295 A100 hours, oracle distillation + GRPO + 72B validation). Total: ~556 A100 hours scoped, ~726 aggressive. Model strategy: Qwen2.5-7B for all ablations (single A100, ~4 min/instance), Qwen2.5-72B (4×A100) for final validation only. $100 API reserved for real prefix cache hit rate measurement (~10 Sonnet instances) and frontier model sanity check (~20 Haiku instances). 13-week timeline. Key decision points identified at 1.1, 1.2, 2.1, 2.2, 3.2. Updated index.md.

## [2026-04-05] synthesis | Importance Scoring + Research Plan

Created importance-scoring.md: 2D scoring framework (importance × stability). 7 signals: Expected Attention (arXiv:2510.00636, closed-form future attention), cumulative past attention (H2O-style), reference counting (novel agentic signal), low-entropy token protection (ForesightKV), structural type prior, importance variance, tool-call dependency graph centrality. Two additional techniques: contrastive deduplication and sink-aware layout (positions 4-100 after sink tokens for free primacy boost). Milestone mapping: structural + reference count for 75%, +attention for 100%, +Expected Attention + learned weights for 125%.

Created research-plan.md: three-phase plan (Phase 1: instrumentation + FIFO/LRU baselines; Phase 2: core AdaptiveCache + hole-leaving eviction options A/B/C; Phase 3: learned signals + compaction sweep). Full baseline table of 9 systems. Key: primary comparison targets are ReSuM training-free (modular competitor) and Context Folding + FoldGRPO (performance ceiling). Key infrastructure question: how to approximate hole-leaving with public Anthropic API.

Updated overview.md: milestones rewritten to match research plan, scoring signals section added. Updated index.md.

## [2026-04-05] synthesis | KV Cache Architecture + AdaptiveCache Refined Design

Created kv-cache-architecture.md. Key finding from web research: mid-sequence KV eviction is feasible without recomputing downstream states — post-rotated RoPE keys mean evicted positions become holes (not renumbered), leaving all other cached states valid. Production evidence: PagedEviction (EACL 2026), Claude Code cache_edits, H2O/SnapKV. Critical constraint: never renumber surviving positions after eviction. "Stateful KV Cache Management" (arXiv:2511.04686) shows positional integrity beats token count.

Updated overview.md with refined AdaptiveCache design: decomposed into two mechanisms — (1) layout optimizer (rare, determines pinned prefix zone for cross-step cache hits) and (2) in-place hole eviction engine (every step, essentially free). Added three-tier pipeline (hole eviction → layout reorganization → emergency summarization). Clarified that layout optimization's purpose is cross-step prefix cache HITS, not making eviction cheap (eviction is already cheap). Updated open questions. Updated index.md.

## [2026-04-04] ingest | Claude Code Compaction (Anthropic, 2025)

Ingested `raw/claude_code_compaction.md` — internal technical reference for Claude Code's compaction pipeline. Created claude-code-compaction.md. Most technically detailed production system documented. Key finding: **cached microcompact** (`cache_edits` API extension) surgically deletes cached KV state of selected tool results without invalidating the rest of the prefix — the one mechanism in any system we've seen that is genuinely prefix-cache-preserving for selective eviction. This is the closest existing mechanism to AdaptiveCache's core idea. Also notable: cheapest-first pipeline architecture (Snip → Microcompact → Context Collapse → Autocompact), forked agent reusing parent's prompt cache for summarization, post-compact attachment re-injection system. Updated: index.md, log.md.

## [2026-04-04] init | Wiki created

Set up wiki structure. Created: overview.md, index.md, context-management.md, prefix-caching.md.

## [2026-04-04] ingest | Context-Folding (Sun et al., 2025)

Ingested `raw/papers/contextFolding.pdf`. Created context-folding.md. Updated: index.md, overview.md (positioning section, comparison table), context-management.md (Context Folding entry in taxonomy). Key finding: Context Folding is complementary to AdaptiveCache — coarse sub-trajectory level vs fine-grained layout optimization. Strong baseline to cite and beat on SWE-bench.

## [2026-04-04] ingest | ReAct (Yao et al., ICLR 2023)

Ingested `raw/papers/react.pdf`. Created react.md. ReAct is the primary AdaptiveCache baseline — full context append, Thought-Action-Observation loop, no layout awareness, no KV-cache optimization. Context grows as ct = (task, Th1, A1, Ob1, ...). AdaptiveCache is a drop-in layer that does not alter the ReAct loop. Updated: index.md (Sources table, tags), context-management.md (added link, updated comparison matrix).

## [2026-04-04] ingest | Scaling LLM Multi-Turn RL with End-to-End Summarization (Lu et al., 2025)

Ingested `raw/papers/summarization-rl.pdf`. Created summarization-rl.md. SUPO formalizes periodic summarization as a summarization-augmented MDP, trains with GRPO-style RL; +3.2% CodeGym, +14.0% BrowseComp-Plus. Key AdaptiveCache contrast: SUPO's summarization step generates new text that invalidates the prefix KV-cache — AdaptiveCache avoids this by reordering existing tokens without generation. Added taxonomy entry 4a (RL-Trained Summarization Policy). Updated: index.md, context-management.md.

## [2026-04-04] ingest | MEM1 (Zhou et al., 2025)

Ingested `raw/papers/mem1.pdf`. Created mem1.md. MEM1 replaces all prior context with a fresh `<IS>` internal state each turn; PPO-trained with 2D attention mask; 3.5× memory reduction, 3.7× perf gain on 16-objective tasks. Key AdaptiveCache contrast: new `<IS>` text each turn = zero prefix KV-cache reuse across turns; training-free application of MEM1 actually hurts performance. Added taxonomy entry 4b. Updated: index.md, context-management.md.

## [2026-04-04] ingest | MemAgent (Yu et al., 2025)

Ingested `raw/papers/memagent.pdf`. Created memagent.md. MemAgent processes input in chunks with a 1024-token overwrite memory block; Multi-Conv DAPO RL; O(N) complexity; 8K context trained on 32K, extrapolates to 3.5M tokens with <5% loss. Key AdaptiveCache contrast: designed for static document reading not dynamic agentic loops; overwrite memory destroys cross-chunk prefix cache continuity. Added taxonomy entry 4c. Updated: index.md, context-management.md.

## [2026-04-04] ingest | OpenHands Context Condensation (All-Hands.dev, 2025)

Fetched from GitHub source (`openhands/memory/condenser/`). Created openhands-condensation.md. Most widely deployed production example of summarization-based context management. 9 pluggable strategies including LLM summarizing, attention-based, structured summary, window/recency, and amortized forgetting — composable via pipeline. Key finding: all LLM-based strategies generate new summary tokens, invalidating prefix KV-cache — same structural weakness as all summarization approaches. Noted pipeline composition as a design pattern AdaptiveCache could learn from. Updated: index.md (backlog cleared).

## [2026-04-04] ingest | ReSuM (Wu et al., 2025)

Ingested `raw/papers/resum.pdf`. Created resum.md. ReSuM is a plug-and-play external summarizer (ReSumTool-30B); +4.5% training-free over ReAct, +8.2% with ReSum-GRPO (advantage broadcasting). Key AdaptiveCache contrast: closest competitor in modularity (both are drop-in, no agent retraining), but summarization generates new tokens that invalidate prefix KV-cache — AdaptiveCache is prefix-cache-preserving. MEM1 training-free hurts; ReSuM training-free helps. Training-free ReSuM is the primary empirical baseline for AdaptiveCache to beat. Updated: index.md, context-management.md.

## [2026-04-04] ingest | H2O (Zhang et al., NeurIPS 2023)

Ingested `raw/papers/h2o.pdf`. Created h2o.md. H2O introduces heavy-hitter oracle KV eviction: power-law cumulative attention distribution means a small "H2" set captures most attention mass; greedy submodular eviction with dynamic H2 set + recent window budget; up to 29% latency reduction and 1.9× throughput gain. Key finding: attention-based eviction outperforms recency-only (StreamingLLM-style). Layout gap: surviving tokens scatter at original positions; no cross-call prefix reuse possible. AdaptiveCache uses H2O's cumulative attention scoring as its importance signal, adding the missing layout step.

## [2026-04-04] ingest | StreamingLLM (Xiao et al., ICLR 2024)

Ingested `raw/papers/streamingllm.pdf`. Created streamingllm.md. StreamingLLM discovers attention sinks: initial tokens accumulate disproportionately high attention due to SoftMax normalization needing a "dump" for unneeded attention. 4 sink tokens + rolling window of recent tokens enables infinite streaming at 22.2× speedup over naive dense attention. Key finding: initial tokens are at the primacy position AND are empirically critical — double motivation for stable prefix placement. Layout gap: no cross-call reuse; middle tokens dropped regardless of importance. AdaptiveCache's stable prefix = sinks (via StreamingLLM) + H2 tokens (via H2O).

## [2026-04-04] ingest | ScissorHands (Liu et al., NeurIPS 2023)

Ingested `raw/papers/scissorhands.pdf`. Created scissorhands.md. ScissorHands proves the Persistence of Importance Hypothesis: >95% overlap between first-half and second-half pivotal token sets across OPT-6B through OPT-66B; theoretical bound via skip-connection cosine similarity argument. History window (w=400) tracks low-attention events; evicts highest-count losers. 5× compression with no accuracy drop; 4-bit quantization compatible. Key contribution to AdaptiveCache: the strongest empirical evidence that stable prefix token set is identifiable in advance. Layout gap: decoding-phase only; no cross-call persistence; no layout step.

## [2026-04-04] ingest | SnapKV (Li et al., 2024)

Ingested `raw/papers/snapkv.pdf`. Created snapkv.md. SnapKV addresses prompt-phase KV compression (gap H2O leaves): uses last Lobs tokens as observation window to vote on which prefix positions matter before generation; per-head top-k with 1D max pooling for cluster coherence; >70% hit rate between observation-window selection and generation-time attention. 3.6× generation speedup, 8.2× memory reduction at 16K tokens; robust to lost-in-middle pathology. Key contribution to AdaptiveCache: the observation window mechanism — AdaptiveCache uses last agent step's output as the observation window at each inter-step boundary. Layout gap: per-query scatter; different queries select different token sets; no stable prefix across calls.

## [2026-04-04] ingest | PyramidKV (Cai et al., 2024)

Ingested `raw/papers/pyramidkv.pdf`. Created pyramidkv.md. PyramidKV observes that attention entropy decreases monotonically from lower to upper transformer layers; uniform KV budget wastes slots in upper layers and under-serves lower layers. Per-layer entropy-proportional budget allocation (linear pyramid schedule competitive with full entropy measurement); uses SnapKV observation window within each layer. Outperforms uniform-budget methods on LongBench and Needle-in-a-Haystack. Key contribution to AdaptiveCache: per-layer budget shape is orthogonal and composable with layout optimization; upper-layer concentration quantifies how few tokens needed in stable prefix. Layout gap: same as SnapKV — per-query, per-layer scatter; no cross-step stable prefix.

## [2026-04-04] ingest | Lost in the Middle (Liu et al., TACL 2023)

Ingested `raw/papers/lost-in-middle.pdf`. Created lost-in-middle.md. Proves U-shaped performance curve: GPT-3.5-Turbo accuracy at 75.8% (position 1), drops to 53.8% (position 9), recovers to 63.2% (position 20) in 20-document QA. Pattern appears across GPT-3.5-Turbo, GPT-4, Claude-1.3, MPT-30B, LongChat-13B; only models ≥13B exhibit U-shape (7B purely recency-biased). Extended context window size alone does not fix positional sensitivity. Key contribution to AdaptiveCache: behavioral justification — position is a first-class design variable; primacy + recency bias maps directly to stable prefix + volatile suffix layout. Diagnostic only; no remedy proposed.

## [2026-04-04] ingest | LLMLingua & LongLLMLingua (Jiang et al., Microsoft, EMNLP 2023 / 2024)

Ingested `raw/papers/llmlingua.pdf` and `raw/papers/longllmlingua.pdf`. Created llmlingua.md (combined). LLMLingua: coarse-to-fine compression via small model (Alpaca-7B) perplexity; budget controller + iterative token-level compression + distribution alignment; 20× compression with 1.5-point EM drop; 1.7–5.7× latency reduction. LongLLMLingua: question-aware extension; contrastive perplexity = perplexity(x_i|x_{<i}) − perplexity(x_i|x^que, x_{<i}) ≡ conditional PMI; document reordering exploiting primacy/recency; 21.4% improvement over original prompt at 4× compression; 94% cost reduction on LooGLE. Key finding: LongLLMLingua's document reordering empirically validates AdaptiveCache's layout hypothesis — placing important content early improves performance even at same compression ratio. Layout gap: both produce prefix-invalidating output; different byte sequence every compression event; no cross-step persistence; separate 7B model overhead.

## [2026-04-04] ingest | Infini-Transformer (Munkhdalai et al., Google, April 2024)

Fetched from arXiv 2404.07143 (PDF read failed; used arXiv HTML). Created infini-transformer.md. Infini-attention maintains a compressive memory matrix (d_key × d_value per head) updated via outer-product associative binding at each segment boundary. Delta rule variant: M_s ← M_{s-1} + σ(K)^T(V − σ(K)M_{s-1}/σ(K)z_{s-1}). Learned gating scalar β per head blends local attention with compressive memory retrieval. 1.6M constant memory regardless of sequence length (114× compression vs. Memorizing Transformers); 100% passkey accuracy at 1M tokens; ROUGE 18.5 on BookSum (SOTA at submission). Requires architectural modification + continual pre-training; not prefix-cacheable (recurrent state changes every segment). Key contribution to AdaptiveCache: represents the "compress all" alternative paradigm; demonstrates that compressive memory works at scale but cannot be applied to existing LLMs without modification.

## [2026-04-04] ingest | Anthropic Server-Side Compaction (Beta 2026)

Fetched from Anthropic API documentation. Created anthropic-compaction.md. Beta feature (`compact-2026-01-12` header); supported on Opus/Sonnet 4.6. Automatically summarizes conversation when input exceeds threshold (default 150K, minimum 50K tokens). Compaction block emitted in assistant response; API drops all prior messages when compaction block is appended on next call. Parameters: trigger threshold, pause_after_compaction (hook for custom injection), custom instructions. Billing: additional sampling step billed separately via `iterations` array in usage response. Prompt caching compatible for system prompt (system prompt never compacted). Key finding: Anthropic's own production solution validates the context management problem but still generates new summary tokens, invalidating the conversation prefix KV-cache — the exact gap AdaptiveCache addresses. Updated: index.md, context-management.md, prefix-caching.md.
