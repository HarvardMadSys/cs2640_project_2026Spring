# Phase 1: Enabling Refactors & CourtListener Pilot — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-16
**Phase:** 01-enabling-refactors-courtlistener-pilot
**Areas discussed:** Throughput metric, Results migration, Pilot failure policy, replay_zipf API shape, FNV-1a seeds

---

## Gray Area Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Throughput metric | How to measure and report throughput | ✓ |
| Results migration | What to do with existing results/ contents | ✓ |
| Pilot failure policy | <70% success rate on CourtListener pilot | ✓ |
| replay_zipf API shape | New function signature | ✓ |

**User's choice:** All four areas selected.

---

## Throughput Metric

### Q1: How should throughput (accesses/sec) be measured and reported?

| Option | Description | Selected |
|--------|-------------|----------|
| Per (policy, cache-size) cell | One number per row in mrc.csv | ✓ |
| Per policy only | Averaged across cache sizes | |
| Time-series over the trace | Log every N accesses | |
| All three | Most data | |

**User's choice:** Per (policy, cache-size) cell

### Q2: What time resolution?

| Option | Description | Selected |
|--------|-------------|----------|
| Whole-run wall clock | `std::chrono` around run_simulation | ✓ |
| Exclude CSV write overhead | Wall clock around just access loop | |
| CPU time (user+sys) | Avoids I/O variance | |

**User's choice:** Whole-run wall clock

---

## Results Migration

### Q: What to do with existing results/ when reorganizing?

| Option | Description | Selected |
|--------|-------------|----------|
| Migrate Congress results | `git mv` existing CSVs into results/congress/ | ✓ |
| Wipe and regenerate | Delete old, regenerate in new layout | |
| Keep flat + add subdirs | Legacy + new coexist | |

**User's choice:** Migrate Congress results

---

## Pilot Failure Policy

### Q: If CourtListener pilot finds <70% success, what happens?

| Option | Description | Selected |
|--------|-------------|----------|
| Tune ID ranges + retry pilot | Narrow ranges until ≥70%, block phase completion | ✓ |
| Drop the weak endpoint | Exclude and proceed | |
| Proceed regardless | Filter 404s later | |

**User's choice:** Tune ID ranges + retry pilot

---

## replay_zipf API Shape

### Q1: How should the new replay_zipf API look?

| Option | Description | Selected |
|--------|-------------|----------|
| Two functions | prepare_objects + generate_replay_trace | ✓ |
| Overload | Same name, different signatures | |
| Single struct param | ReplayConfig struct | |

**User's choice:** Two functions

### Q2: FNV-1a seeds for W-TinyLFU's CMS?

| Option | Description | Selected |
|--------|-------------|----------|
| Hardcoded constants | 4 golden-ratio-derived constants in hash_util.h | ✓ |
| Derived from root seed | Mixing from a single seed | |
| User decides | Claude picks defaults | |

**User's choice:** Hardcoded constants

---

## Claude's Discretion

- Specific FNV basis/prime constants (use standard FNV-1a values; any differing primes for the 4 CMS rows are fine)
- File naming (`hash_util.h`)
- Self-test harness for the extracted hash function
- Exact ID-range heuristics for pilot (tune empirically)

## Deferred Ideas

- Doorkeeper — Phase 4
- Production CourtListener collection — Phase 3
- compare_workloads.py — Phase 5
- Multi-seed CIs — Phase 5
- macOS Homebrew libexpat root fix — out of scope
