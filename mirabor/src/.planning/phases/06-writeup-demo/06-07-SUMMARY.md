---
phase: 06-writeup-demo
plan: 07
status: complete
completed: 2026-04-22
requirements: [DOC-04]
---

# 06-07 SUMMARY — Demo Rehearsals & Screen Recording

## What was built

D-18 + D-16 evidence for DOC-04: three end-to-end `demo.sh` rehearsals captured in
`demo-rehearsal.log`, plus one full screen recording saved at
`docs/demo-backup.mov` as the live-demo fallback artifact.

## Task execution

| Task | Type | Status |
|------|------|--------|
| 1: 3 demo rehearsals on target laptop, log to demo-rehearsal.log | checkpoint:human-action | PASS |
| 2: Screen recording → docs/demo-backup.mov | checkpoint:human-action | PASS |
| 3: .gitignore decision based on file size | auto | Branch A (commit, no .gitignore edit) |

## Rehearsal wall-clock times (D-18)

| Rehearsal | Wall-clock | Verdict |
|-----------|-----------|---------|
| 1 | 4s | PASS |
| 2 | 3s | PASS |
| 3 | 4s | PASS |

- All three runs well under the D-15 60s budget (7–15× headroom for narration).
- Variance: 1s across runs — effectively noise-level, confirms the pipeline is stable.
- 6-policy sweep completed each time: LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU.
- W-TinyLFU's admission-filter advantage visible in the live table (0.9054 vs LRU's 0.9835 at 0.1% cache — a ~7.8pp gap at the smallest cache size).
- No failed rehearsal attempts preceded the 3 passes — first-try clean on the target laptop.

## Screen recording (D-16)

- Path: `docs/demo-backup.mov`
- Size: 4.2 MB (4,376,991 bytes)
- Decision: **committed directly** — under the 10 MB threshold (Branch A per Plan 06-07 Task 2 Step 8).
- `.gitignore` not edited (`docs/demo-backup.mov` is not matched by any existing ignore pattern, confirmed via `git check-ignore`).

## Acceptance criteria (Task 3)

| Check | Expected | Actual |
|-------|----------|--------|
| `test -f demo-rehearsal.log` | exit 0 | ✓ (252 lines) |
| `grep -c '^=== Rehearsal ' demo-rehearsal.log` | ≥ 3 | 3 |
| `grep -c '^\[verdict\]     PASS$' demo-rehearsal.log` | ≥ 3 | 3 |
| `grep -qE 'wall-clock: [0-9]+s' demo-rehearsal.log` | exit 0 | ✓ |
| `test -f docs/demo-backup.mov` | exit 0 | ✓ (4.2 MB) |
| Branch A: `git check-ignore docs/demo-backup.mov` exits 1 | exit 1 | ✓ |
| `git check-ignore demo-rehearsal.log` exits 1 | exit 1 | ✓ |

## Key files

- `demo-rehearsal.log` (repo root, 252 lines, 13 KB) — 3 rehearsal blocks with full stdout + wall-clock
- `docs/demo-backup.mov` (4.2 MB) — QuickTime-recorded end-to-end run, committed to git

## Notes

- The `=== Rehearsal N` header in the first entry (literal `N` rather than `1`) is cosmetic; grep acceptance (`^=== Rehearsal `) still matches all three headers.
- Rehearsal 1's block in the log was generated before the user noticed the header-literal issue; leaving it in preserves the authentic "first run" evidence.
- Screen recording is silent (demo is non-interactive output); size efficiency comes from low-motion terminal content.
