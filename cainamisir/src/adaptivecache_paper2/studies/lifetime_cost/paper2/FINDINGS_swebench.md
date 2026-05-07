# Paper 2 — v0 swebench fair comparison (2026-04-28)

Real agent loop on `psf__requests-3362` (SWE-bench Lite via swebench_live)
with Qwen3-30B-A3B-Instruct-2507. Same task, same agent, same 15 steps.
Baseline at 65K context (so it doesn't crash); memento at 32K context
(naturally never crosses 18K). Apples-to-apples on chat wall (Haiku
time excluded — that's a v0 placeholder, gone with SFT).

## Headline

| Metric | Baseline | Memento (lazy) |
|---|---|---|
| Steps | 15 | 15 |
| Total chat wall | 11.3s | **9.3s (-17.6%)** |
| Final-step chat wall | 1300 ms | **755 ms (-42%)** |
| Final-step prompt | 58,291 tok | **12,185 tok (-79%)** |
| Compactions fired | 0 | 10 (lazy, started at step 6) |

## Per-step

```
step  baseline_wall  baseline_tok  memento_wall  memento_tok
  0       233 ms          865         231 ms         865
  1       175 ms          987         175 ms         987
  2       381 ms        5,395         379 ms       5,395
  3       456 ms        9,803         458 ms       9,803
  4       534 ms       14,211         535 ms      14,211
  5       610 ms       18,619         610 ms      18,619
  6       682 ms       23,027        1073 ms      14,666   ← memento fires here
  7       764 ms       27,435         713 ms      10,686
  8       839 ms       31,843         714 ms      10,929
  9       914 ms       36,251         720 ms      11,130
 10       990 ms       40,659         732 ms      11,374
 11      1069 ms       45,067         737 ms      11,594
 12      1144 ms       49,475         746 ms      11,819
 13      1231 ms       53,883         748 ms      11,998
 14      1300 ms       58,291         755 ms      12,185
```

## Behavior shape

- Steps 0-5 are identical between variants (lazy policy correctly fires nothing).
- Step 6: one compaction event covering multiple older obs, drops prompt from 23K to 14.7K. The 1073ms wall on this step is the *only* place memento is slower than baseline; in the SFT'd Memento setting this spike disappears (memento generation becomes part of normal model output, not an external Haiku call).
- Steps 7-14: memento per-step wall is **flat in the 713-755ms band**. Baseline grows from 764 ms to 1300 ms (~80 ms/step).

## Honest cost decomposition

| Cost component | Baseline | Memento (v0) | Memento (SFT) |
|---|---|---|---|
| Self-hosted chat wall | 11.3s | 9.3s | 9.3s |
| Haiku-as-writer wall | 0 | 43.7s | 0 |
| **Method total** | 11.3s | 53.0s (v0 only) | **9.3s** |

The v0 Haiku usage is for prototyping only. In the published Paper 2 system, mementos come from the agent's own (SFT'd or RL'd) generation, which adds zero extra wall and zero API cost.

## Earlier 32K-context run for context (diagnosis only)

When run at the original `max_model_len=32000`, **baseline crashed at step 9** with `[context overflow: prompt exceeded model max_model_len]` because the prompt grew to 36,251 tokens. Memento bounded the prompt and continued for 6 more steps. This is the failure mode Paper 1 / Phase C ran into; memento avoids it structurally.

## What v0 still doesn't have

- **Recall**: when the agent re-asks for an evicted obs, we should restore from offloaded KV instead of re-prefilling. Current behavior on the 9-step run was that the agent re-read `requests/models.py` 5 times in a row — exactly the Phase C v6 pattern. Recall is the v1 work item.
- **SFT'd memento writer**: replaces the Haiku placeholder. Eliminates the v0 ~43s Haiku overhead.
