# Cache-Informed Prompting for Recursive Language Models

**Student:** Fucheng Warren Zhu (`wzhu@college.harvard.edu`, GitHub: `WarrenZhu050413`)
**Course:** COMPSCI 2640 — Modern Computer Storage Systems, Spring 2026

## Research question

Does injecting cache-eviction theory (LRU, S3-FIFO, SIEVE, cost-benefit reasoning) into the RLM root system prompt improve accuracy and/or cost on RLM's own benchmarks?

## Files

- `report.pdf` — 3-page final report.
- `ai-usage.md` — AI coding reflection.
- `src/experiments/` — prompt arms (A0–A6), 3 baselines, benchmark adapters, runners, aggregators, analysis scripts.
- `src/rlm_backend/agent_sdk.py` — backend addition wrapping `claude_agent_sdk.query()` so RLM's sync `completion()` path works under Max-subscription OAuth (no raw Anthropic API key).

## Full source repository

Experiments build on a fork of [alexzhang13/rlm](https://github.com/alexzhang13/rlm). The full working tree (45 commits, preserved history, all results, slides, raw JSONL traces) lives at:

**https://github.com/WarrenZhu050413/rlm/tree/cs2640-cache-informed**

The `src/` here is the slim version: experiment code + the one backend file added to upstream RLM. To reproduce end-to-end, clone the full fork above.

## Build / run

```bash
git clone -b cs2640-cache-informed https://github.com/WarrenZhu050413/rlm.git
cd rlm
uv sync
export CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat01-...'   # Max subscription OAuth
make smoke                                          # quick sanity run
make a0-baseline                                    # vanilla RLM control
make a4-cache-aware                                 # cache-informed arm
```

Per-arm Makefile targets are listed in `CS2640_README.md` at the fork root. Aggregated results live under `results/`, final tables under `report/tables/`.

## Benchmarks

OOLONG, OOLONG-Pairs, BrowseComp+, LongBench-v2 CodeQA, S-NIAH.

## Auth

All Sonnet calls go through `claude_agent_sdk` + `CLAUDE_CODE_OAUTH_TOKEN` (Max subscription). The Agent SDK injects a ~16K Claude Code preamble, constant across all arms — relative comparisons valid, absolute costs footnoted in Table 1.
