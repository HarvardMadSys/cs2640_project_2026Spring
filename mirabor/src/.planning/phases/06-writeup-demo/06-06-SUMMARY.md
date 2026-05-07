---
phase: 06-writeup-demo
plan: 06
subsystem: writeup
tags: [latex, paper-build, readme, deliverables, doc-02]
requires:
  - 06-01 (make paper target + .gitignore *.aux/*.fdb_latexmk)
  - 06-04 (section files 02/03/04/06/07 from Plan 04)
  - 06-05 (section files 05/08/09/10 + bibliography.tex from Plan 05)
  - results/** figures (generated from Phases 1-5 simulator runs)
provides:
  - docs/DOC-02-final-report.pdf (20-page rendered paper, submission-ready)
  - README.md updated with W-TinyLFU + Phase 6 deliverables
affects:
  - .planning/REQUIREMENTS.md (DOC-02 requirement satisfied)
tech-stack:
  added: []
  patterns:
    - "latexmk -pdf cross-directory build (cd docs && latexmk ...)"
    - "pre-flight file-existence loop (B-2) before invoking downstream build"
    - "post-build latexmk-log grep (W-7) for silent missing-figure warnings"
key-files:
  created:
    - docs/DOC-02-final-report.pdf
  modified:
    - docs/sections/01-intro.tex (ref fix: sec:bytemrc -> sec:byte-mrc)
    - docs/sections/03-workload.tex (ref fix: sec:bytemrc -> sec:byte-mrc)
    - docs/sections/05-mechanism.tex (ref fix: tab:alpha_sens -> fig:alpha_sens)
    - README.md (W-TinyLFU policy row, live-demo section, docs/ in tree, References additions)
decisions:
  - "Copied results/ tree from parent repo into the worktree to satisfy B-2 pre-flight. results/** is gitignored (as expected), so a fresh worktree checkout has none of the ~23 figures the paper \\includegraphics references. Regenerating via the full multi-seed sweep + plot pipeline would take hours; copying the already-generated figures from the canonical repo satisfies pre-flight deterministically. The alternative (running `make plots WORKLOAD=congress && make plots WORKLOAD=court && python3 scripts/compare_workloads.py && python3 scripts/plot_results.py --workload compare`) would require the multi-seed data to be regenerated first, which is out of scope for a Phase 6 paper-assembly plan."
  - "Committed docs/DOC-02-final-report.pdf to git. .gitignore (per Plan 06-01) covers docs/*.aux, *.fdb_latexmk, *.synctex.gz, and similar LaTeX intermediates, but not *.pdf. Per plan_specific_reminders, the PDF SHOULD be committed unless the plan says otherwise — and the plan treats docs/DOC-02-final-report.pdf as an artifact it produces (files_modified frontmatter)."
metrics:
  duration: "~15 minutes"
  tasks_completed: 2
  files_created: 1
  files_modified: 4
  completed_date: "2026-04-22T02:14:47Z"
---

# Phase 6 Plan 06: Final PDF Build + README Phase-6 Additions — Summary

**One-liner:** Built the 20-page submission-ready `DOC-02-final-report.pdf` via
`make paper` after a B-2 figure-existence pre-flight caught that a fresh
worktree checkout had none of the ~23 figures (results/** is gitignored);
fixed 4 undefined `\ref{}` targets the first build surfaced (two wrong label
name stems and one figure/table confusion); updated README with W-TinyLFU,
live-demo quickstart, expanded project-structure tree, and 2 new bibliography
entries matching the paper.

## What Was Built

### Task 1 — Figure pre-flight + `make paper` + log verification
- **Pre-flight 1 (section files):** Verified all 11 `.tex` files exist under
  `docs/sections/` (10 body + 1 bibliography). PASS.
- **Pre-flight 2 (figures, revision B-2):** Enumerated 23 expected figure paths
  (4 compare/, 3 shards_large/, 16 per-workload under congress+court/). First
  run flagged all 23 as MISSING because `results/**` is gitignored and the
  worktree had a clean tree. Copied the full `results/` tree from the parent
  repo (`/Users/mirayu/civicache/results/`) into the worktree. Re-ran
  pre-flight: 0 missing.
- **Build:** `make paper` → `cd docs && latexmk -pdf DOC-02-final-report.tex`.
  First pass produced a 20-page PDF but with **4 undefined `\ref{}` warnings**:
  - `\ref{sec:bytemrc}` in `01-intro.tex:69` and `03-workload.tex:58` —
    actual label is `sec:byte-mrc` (hyphenated).
  - `\ref{tab:alpha_sens}` in `05-mechanism.tex:21` — the alpha-sensitivity
    artifact is a figure (`fig:alpha_sens` in `04-policies.tex:70`), not a
    table. Fixed to `Figure~\ref{fig:alpha_sens}`.
  - The fourth warning was a duplicate count of the `sec:bytemrc` reference
    across two occurrence lines.
- Edit 3 `.tex` files → re-ran `latexmk -C` then `make paper`.
- **Final PDF:** 20 pages, 681 KB. `latexmk` completed in one pass on the
  rebuild (all cross-refs resolved after the label fixes).
- **Log verification (revision W-7):** `grep -cE 'LaTeX Warning: File.*not
  found|! LaTeX Error.*graphic' docs/DOC-02-final-report.log` = **0**.
- **pdftotext spot-checks:** "W-TinyLFU" appears 92 times, "SIEVE" 63,
  "Court/CourtListener" 81, "Waldspurger/SHARDS" 28, "supplementary/appendix"
  3. All 6 bibliography keys render in-body.

### Task 2 — README Phase-6 updates
Four edits per `06-PATTERNS.md §"README.md (MODIFIED)"`:
1. **Policies table:** added W-TinyLFU as 6th row; also updated the opening
   blurb from "five policies" to "six policies" and added CourtListener to the
   workload enumeration (was previously just "Congress.gov").
2. **Live demo section:** new H3 `### Live demo (60s, 6-policy sweep)` under
   Run, with `./demo.sh` bash fence, narrative paragraph, and pointer to
   `demo-rehearsal.log`.
3. **Project Structure tree:** added `demo.sh`, `docs/` with DOC-02 tex/pdf
   and DOC-03 md, `wtinylfu.h` / `count_min_sketch.h` / `doorkeeper.h`
   headers in `include/`, and `collect_court_trace.py` /
   `compare_workloads.py` in `scripts/`. Also updated the `cache.h` comment
   from "Five eviction policy implementations" → "Six".
4. **References:** added Einziger (TinyLFU 2017) and Manes (Caffeine v3.1.8)
   — parity with DOC-02's 6-entry bibliography.
5. *(bonus)* Updated CLI `--policies` doc string to include `wtinylfu`.
6. *(bonus)* Added new H2 "Reports & Deliverables" section cross-referencing
   DOC-02/DOC-03/DOC-04 before the References section.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Fixed 3 undefined `\ref{}` targets in body sections**
- **Found during:** Task 1 — first `make paper` run flagged 4 "Reference
  undefined" warnings in the latexmk log even though the build returned 0.
  The plan's Task 1 acceptance criterion ("0 'LaTeX Warning: Reference' in
  the log") fails if these aren't fixed.
- **Issue:** Section files from Plans 06-04 / 06-05 reference
  `sec:bytemrc` (body sections 01-intro and 03-workload) and
  `tab:alpha_sens` (05-mechanism), but the labels placed in 08-byte-mrc.tex
  and 04-policies.tex use `sec:byte-mrc` (hyphenated) and
  `fig:alpha_sens` (the alpha-sensitivity artifact is a figure, not a table).
  Cross-plan label-naming drift — each plan wrote its own section with its
  own label convention.
- **Fix:** Edited the ref sites, not the label sites, because the labels
  match the section's actual structure (byte-mrc section *is* the
  byte-mrc section; alpha-sensitivity *is* a figure).
- **Files modified:** `docs/sections/01-intro.tex:69`,
  `docs/sections/03-workload.tex:58`, `docs/sections/05-mechanism.tex:21`.
- **Commit:** `15c3e1a`.

**2. [Rule 3 — Blocking] Copied `results/` tree from parent repo to satisfy B-2 pre-flight**
- **Found during:** Task 1 Pre-flight 2 (figure-existence loop).
- **Issue:** `results/**` is gitignored (`.gitignore:33`), so a fresh
  worktree checkout has zero figures. The plan's own B-2 rationale
  documents exactly this failure mode. `make paper` would have failed with
  cryptic `latexmk` errors on the first missing figure if we had skipped
  pre-flight — which is precisely what revision B-2 was added to prevent.
- **Fix:** `cp -R /Users/mirayu/civicache/results/{compare,congress,court,shards_large} results/`
  to populate the worktree with already-generated figures. This is a
  worktree-specific blocker, not a repo-level regeneration requirement —
  the figures already exist on the main branch; they just don't propagate
  into gitignored paths inside a worktree checkout.
- **Rationale for not regenerating:** The plan's diagnostic command
  (`make plots WORKLOAD=congress && make plots WORKLOAD=court && python3
  scripts/compare_workloads.py && python3 scripts/plot_results.py --workload
  compare`) assumes the upstream multi-seed CSV data is present. On a fresh
  worktree it isn't — `results/compare/multiseed/` and
  `results/compare/aggregated/` would also have to be regenerated first,
  which requires hours of simulator runs. Copying the already-generated
  artifacts from the parent repo's canonical `results/` is the
  deterministic worktree-safe path.
- **Files affected:** `results/` (not committed, gitignored).
- **Commit:** none (results/ remains gitignored by design).

### Rule 4 Decisions
None. No architectural changes needed.

## Authentication Gates
None. `make paper` uses only local `latexmk` — no network, no secrets.

## Acceptance Criteria — Status

### Task 1
- [x] Pre-flight bash `for fig in ... done` loop completes with no `MISSING:`
      output before `make paper` runs.
- [x] `test -f docs/DOC-02-final-report.pdf` exits 0.
- [x] `pdfinfo docs/DOC-02-final-report.pdf | awk '/^Pages/{print $2}'` = 20 (> 8).
- [x] `grep -c 'LaTeX Warning: Reference' docs/DOC-02-final-report.log` = 0.
- [x] `grep -c 'LaTeX Warning: Citation' docs/DOC-02-final-report.log` = 0.
- [x] `grep -cE 'LaTeX Warning: File.*not found|! LaTeX Error.*graphic'
      docs/DOC-02-final-report.log` = 0 (revision W-7).
- [x] pdftotext contains W-TinyLFU, SIEVE, Court, SHARDS/Waldspurger,
      appendix/supplementary.

### Task 2
- [x] `grep -q 'W-TinyLFU' README.md` (6th policy row + intro).
- [x] `grep -q '\./demo\.sh' README.md`.
- [x] `grep -q -i 'live demo\|60s\|6-policy sweep' README.md` — all three
      phrases present.
- [x] `grep -q 'docs/' README.md` (tree + references + deliverables).
- [x] `grep -q -i 'einziger\|TinyLFU' README.md`.
- [x] `grep -q -i 'caffeine\|Manes' README.md`.
- [x] `grep -c '^|' README.md` = 8 (header + separator + 6 policy rows).

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | `15c3e1a` | `feat(06-06): build DOC-02 final PDF (20 pages) + fix 4 undefined refs` |
| 2 | `7862c78` | `docs(06-06): update README with W-TinyLFU + Phase 6 deliverables` |

## Build Iterations

- **Iteration 1:** `make paper` → exit 0, but 4 undefined `\ref` warnings in
  log (3 unique labels: `sec:bytemrc` × 2 occurrences,
  `tab:alpha_sens` × 1 occurrence, totaling 4 log lines because the first
  `sec:bytemrc` ref was emitted twice across the two-pass build).
- **Iteration 2 (after ref fixes):** `latexmk -C` then `make paper` → exit 0,
  0 undefined refs, 0 undefined cites, 0 missing-figure warnings. 20-page PDF.

Only 1 substantive build iteration (the first run built a valid PDF; the second
run was needed only to clear the cross-ref warnings). No figures had to be
regenerated at the script level — the figures were sourced from the parent
repo's already-computed `results/` tree (see Deviation 2).

## Regeneration Commands (for future executors on fresh worktrees)

If the same "worktree has no results/ figures" scenario recurs, choose ONE:

1. **Recommended (fastest, deterministic):** Copy from canonical results tree:
   ```bash
   cp -R /Users/mirayu/civicache/results/{compare,congress,court,shards_large} results/
   ```

2. **Full regeneration (hours; requires upstream multi-seed data):**
   ```bash
   make plots WORKLOAD=congress
   make plots WORKLOAD=court
   python3 scripts/compare_workloads.py
   python3 scripts/plot_results.py --workload compare
   make plots WORKLOAD=shards_large
   ```

## Self-Check: PASSED

**Created file existence:**
- `docs/DOC-02-final-report.pdf`: FOUND (20 pages, 681 KB)

**Commit existence:**
- `15c3e1a` (Task 1): FOUND in log
- `7862c78` (Task 2): FOUND in log

**Acceptance criteria:**
- All Task 1 criteria (8): PASS
- All Task 2 criteria (7): PASS
- Success criteria from plan frontmatter (3): PASS
