# Overnight session 2026-04-28 — wake-up summary

You went to sleep saying "AdaptiveCache heuristic should not really work I think. What directions shall we explore?" Here's where things landed.

## The honest result

**Heuristic compaction does not beat `none` on SWE-bench Lite at this scale (N=4 tasks).** All 5 compaction policies (`position_aware`, `prefix_preserving`, `microcompact`, `evict_oldest`, `llm_reorganizer`) cluster at 1/4 resolved while `none` gets 2/4. Per-task analysis shows compaction *structurally works* (prevents context overflow on tasks `none` can't handle) but the dropped tool obs costs the agent extra steps, and `max_steps=40` cuts those recoveries off before resolution.

This validates your "AdaptiveCache heuristic should not really work" intuition — at least for *training-free heuristic eviction on a tight step budget*. The negative result motivates Paper 2 (Memento-style KV recall: keep the bytes, just offload them).

## What's new in the codebase

**Benchmarks**:
- `pipeline/benchmarks/swebench_live.py` — real SWE-bench Lite live agent runs without docker. Agent has `read_file`, `list_files`, `search`, `edit_file`, `run_tests`, `submit`. Resolve via git-diff overlap with gold patch (loose oracle, adequate for ranking).

**Policies**:
- `pipeline/policies/evict_oldest.py` — pure-eviction baseline, no LLM, hole-leaving placeholder.
- `pipeline/policies/llm_reorganizer.py` — Paper 1's actual claim. Small LLM (Qwen3-4B) scores each tool obs 1-10, drops bottom-K via hole-leaving.
- `pipeline/policies/position_aware.py` — fixed to use `[evicted]` placeholders instead of deleting messages (was leaving dangling `tool_call_id` refs in the chat template).
- `pipeline/policies/prefix_preserving.py` — added `cooldown_steps` to prevent rapid-fire compaction.

**Runner / harness**:
- `pipeline/runner.py` — added "kick reminder" path for tool-only envs (SWE-bench-style). When agent emits plain text without calling a tool but `submit` exists, append a one-line "please call a tool" user-role message and continue. Without this, 2 of 4 `none` tasks failed at step 4.
- `pipeline/models/vllm_local.py` — catches `ValueError("longer than the maximum")` from vLLM context check; returns sentinel completion so the matrix doesn't crash on `none` policy overflows.
- `pipeline/policies/base.py` — `CompactionContext` now exposes `summarizer_model` (raw `ChatModel`), so policies can issue arbitrary prompts (used by `llm_reorganizer`'s scoring call).
- `pipeline/harness.py` — `summarizer_model` plumbing through config to per-task model build.

**Configs**:
- `configs/phase_c_swebench_live.yaml` — Phase C: 6 policies × 4 SWE-bench Lite instances.
- `configs/phase_c_v2_max80.yaml` — Phase C v2: 3 policies × 2 informative tasks, `max_steps=80` (testing whether compaction wins given recovery room). **May still be running when you wake up.**

## Key artifacts to look at

- **`studies/lifetime_cost/reports/PHASE_C_REPORT.md`** — full writeup with per-task breakdown, Pareto analysis, and explicit "what's actually doing the work" / "what to try next" sections. Start here.
- `studies/lifetime_cost/out/phase_c_swebench_live/figures/summary.csv` — the canonical numbers.
- `studies/lifetime_cost/out/phase_c_swebench_live/figures/phase_c_summary.json` — programmatic summary with Pareto-frontier annotation.
- `studies/lifetime_cost/scripts/analyze_phase_c.py` — re-runnable analysis: `python -m studies.lifetime_cost.scripts.analyze_phase_c --out_dir <out_dir>`.

## What I ran while you slept (v2 max_steps=80)

`phase_c_v2_max80.yaml` finished. **The hypothesis is confirmed but the result is still honest-negative**: with `max_steps=80`, `evict_oldest` now *matches* `none` on resolve rate (both 1/2 — `evict_oldest` resolves `requests-2317` at step 67 where v1's 40-step cap had cut it off). But it still **costs 2.8× more** because compaction added 41 extra steps on the task `none` solves in 26.

| Policy | Resolved | $/resolved | Steps | Total prompt tokens |
|---|---|---|---|---|
| `none` | 1/2 | $0.054 | 53 (avg) | 489k |
| `evict_oldest` | **1/2** (matched `none`) | **$0.150** (lost on cost) | 73 (avg) | 802k |
| `llm_reorganizer` | 0/2 | ∞ | 80 (avg) | 2,872k |

The honest finding: **dropping tool obs forces enough re-exploration that the extra step cost dominates the per-token savings** — even when the dropped bytes were genuinely redundant. This is exactly the negative result that motivates Paper 2 (Memento-style KV recall: keep the bytes via offload, not by deletion). Updated report has a full addendum.

`requests-3362` is still unsolved by everyone — the bug is just hard for Qwen3-30B regardless of context budget. To get clean compaction-vs-`none` data on that task, need a stronger model or a better resolve oracle.

## What I think you should do next

In priority order (assuming you want to keep pushing for a Pareto-winning result):

1. **Look at the v2 max_steps=80 result first.** If that flips the picture, we have a real story. If not, the issue is deeper than step budget.
2. **Bump `max_model_len` to 80K and re-run Phase C v1.** Right now `none` is failing 2/4 due to context overflow that *should* be solvable; lifting that ceiling makes the comparison cleaner (compaction-vs-`none` becomes a quality contest, not a survival contest).
3. **Scale to N=20 tasks with multiple seeds.** A 4-task sample × a coarse line-overlap oracle = lots of noise. The Paper 1 figure needs at least N=20 per cell, ideally with the real `swebench` docker harness for `test_patch` execution.
4. **Tune `llm_reorganizer`** — at `drop_count=2` it fired 91× in Phase C v1 and still hit overflow on 3 of 4 tasks. Currently retrying at `drop_count=6` in v2. If that's also not enough, the scoring prompt itself needs revising (currently a generic "is the agent likely to need this later"; could be made task-aware by including the problem statement).
5. **Build the actual AdaptiveCache layout-optimizer policy.** All current policies are baselines. The thesis policy (joint layout reorganization + hole-leaving eviction) is a 100%-milestone item per `wiki/research-plan.md`; it requires attention access (not exposed by vLLM by default, but vLLM has hooks if we want them).

## What did NOT work tonight (logged so we don't redo)

- **`taubench_multi.py`** (multi-customer τ-bench session benchmark) — drafted but didn't ship; pivoted to SWE-bench live per your "I think a coding agent task around 30-40-50k tokens" guidance. File deleted.
- **`Qwen/Qwen3-1.7B-Instruct-2507`** as summarizer — that exact repo doesn't exist on HF. Switched to `Qwen/Qwen3-4B-Instruct-2507` (which exists). Pricing.yaml updated.
- **Tighter compaction thresholds on stock τ-bench airline** — Phase B smoke v3-v5 explored this exhaustively; conclusion is τ-bench airline has nothing to compact (max tool obs = 953 tokens, contexts fit in 16K, cache is naturally 95% hit). Memory entry: `phase_c_findings.md`.

## Memory entries written

- `qwen3_respond_gotcha.md` — Qwen3 plain-text → respond fallback
- `blackwell_vllm_setup.md` — vLLM 0.10.2 + V0 engine for CUDA 12.8 + sm_120
- `compaction_taubench_dead.md` — τ-bench airline isn't a compaction benchmark
- `swebench_live_setup.md` — how the new SWE-bench Lite live mode works
- `project_paper1_goal.md` — Paper 1 Pareto headline figure target
- `phase_c_findings.md` — last night's experimental result
- `user_vlad.md`, `user_collab_style.md` — about you

Tests: 17/17 still pass.

— Claude (overnight)
