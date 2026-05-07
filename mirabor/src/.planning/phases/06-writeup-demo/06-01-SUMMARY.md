---
phase: 06-writeup-demo
plan: 01
subsystem: paper-scaffold
tags: [latex, makefile, scaffold, interface-first, doc-02]
dependency-graph:
  requires:
    - results/compare/figures/compare_mrc_2panel.pdf (page-1 figure target; produced by Phase 5)
  provides:
    - docs/DOC-02-final-report.tex (main LaTeX file with \input{} skeleton for 9 body sections + appendix + bibliography)
    - docs/sections/01-intro.tex (opening hook with headline finding + Figure 1 xref)
    - docs/sections/bibliography.tex (6 canonical citations: Zhang/Yang/Waldspurger/Einziger/Caffeine/Clauset)
    - Makefile `paper` target (latexmk -pdf build recipe)
    - Makefile `.PHONY` line owns both `paper` and `demo` tokens (ownership boundary for Plan 02)
    - .gitignore entries for docs/ LaTeX build artifacts
  affects:
    - Plans 06-04 and 06-05 (they author sections 02-09 + 10-appendix.tex into this \input{} skeleton)
    - Plan 06-02 (must NOT touch .PHONY line; only appends `demo:` target body after the `paper:` target)
tech-stack:
  added:
    - LaTeX (article class 11pt letterpaper) — scaffold only; build invocation lives in Makefile
    - latexmk build tool (invoked via `make paper` from docs/)
  patterns:
    - modular \input{sections/NN-name} composition (9 body + 1 appendix + 1 bibliography = 11 includes)
    - figure-led page-1 opening per D-04 (compare_mrc_2panel.pdf + callout caption)
    - two-column bibliography via multicol + thebibliography (shape copied from v7.tex analog)
    - first-person methodology / third-person results register per D-02
key-files:
  created:
    - docs/DOC-02-final-report.tex (61 lines)
    - docs/sections/.gitkeep (empty; makes sections dir committable)
    - docs/sections/01-intro.tex (72 lines, 570 words of prose)
    - docs/sections/bibliography.tex (15 lines, 6 bibitems)
  modified:
    - Makefile (+10 lines: .PHONY tokens `paper`+`demo` and new `paper:` target block)
    - .gitignore (+10 lines: Phase 6 LaTeX artifacts block under docs/)
decisions:
  - "Adopted verbatim preamble from 06-PATTERNS.md (lines 40-57): matches the working progress.tex/progress.pdf toolchain on the target laptop; no new LaTeX-environment risk."
  - "Ordered \\appendix + \\input{sections/10-appendix} BEFORE \\input{sections/bibliography} so the appendix figures and reproducibility material render before the bibliography per D-11 — bibliography remains the final element on the PDF."
  - "Added `\\input{sections/10-appendix}` as a stub reference (not an empty placeholder file): Plan 05 Task 4 authors 10-appendix.tex. `make paper` intentionally fails until the Wave 2 body plans land — that's the interface-first contract."
  - "Per revision B-1 ownership refactor, Plan 06-01 owns the Makefile .PHONY line edit. Both `paper` AND `demo` tokens declared in a single atomic edit. Plan 06-02 will NOT touch this line — it only appends the `demo:` target body after the `paper:` target. This preserves Wave 1 parallelism by eliminating the .PHONY-line race."
  - "Padded intro to 570 words (exceeds the 200-word acceptance minimum by ~2.8×) so the section carries the full mechanism foreshadowing; Plans 04/05 still have latitude to trim during final polish."
  - "Made docs/*.aux etc. gitignore entries explicit even though they're matched by the existing *.aux top-level glob. Redundant-but-documentary per acceptance criteria; helps future readers grep for docs/ specifically."
metrics:
  duration_sec: 58
  commits: 3
  completed: 2026-04-22T01:43:22Z
  tasks_completed: 3
  files_created: 4
  files_modified: 2
---

# Phase 6 Plan 01: Paper LaTeX Scaffold Summary

Created the LaTeX paper skeleton for DOC-02 — preamble, title, page-1
figure-led opening, 9-section `\input{}` chain, D-11 appendix slot, and
two-column bibliography — plus the `make paper` build recipe and Phase-6-
specific gitignore entries. Plans 04 + 05 (Wave 2) now have a stable
`\input{}` contract to write section bodies into without touching the
main .tex file or each other's files.

## What Was Built

| Artifact                              | Purpose                                                                                    | Lines |
|---------------------------------------|--------------------------------------------------------------------------------------------|-------|
| `docs/DOC-02-final-report.tex`        | Main LaTeX file — preamble, title, page-1 figure, 11 `\input{}` stubs (9 body + appendix + bib) | 61    |
| `docs/sections/.gitkeep`              | Empty file so the sections/ dir is trackable before Plan 04/05 populate it                 | 0     |
| `docs/sections/01-intro.tex`          | Opening section — D-01 surprise-finding hook, Figure 1 xref, 4-contribution list, section map | 72    |
| `docs/sections/bibliography.tex`      | Two-column thebibliography with 6 canonical bibitems                                       | 15    |
| `Makefile` (modified)                 | Added `paper:` target + extended `.PHONY` with `paper`+`demo`                              | +10   |
| `.gitignore` (modified)               | Added Phase 6 LaTeX-artifact block for `docs/`                                             | +10   |

## The \input{} Contract — What Plans 04 + 05 Must Honor

The main file `docs/DOC-02-final-report.tex` pulls in section modules
exactly in this order; downstream plans must write files with these exact
paths:

```
\input{sections/01-intro}        Plan 06-01 (this plan, Task 2)
\input{sections/02-related}      Plan 06-04
\input{sections/03-workload}     Plan 06-04
\input{sections/04-policies}     Plan 06-04
\input{sections/05-mechanism}    Plan 06-05  (centerpiece per D-03)
\input{sections/06-shards}       Plan 06-04
\input{sections/07-ablations}    Plan 06-04
\input{sections/08-byte-mrc}     Plan 06-05
\input{sections/09-conclusion}   Plan 06-05
\appendix
\input{sections/10-appendix}     Plan 06-05 Task 4  (D-11: 10-12 figures)
\input{sections/bibliography}    Plan 06-01 (this plan, Task 2)
```

Ordering rule: `\appendix` + `\input{sections/10-appendix}` appear
**before** the bibliography `\input`, so the final PDF lays out as
Body → Appendix → Bibliography. This is the locked ordering per D-11.

Plans 04 and 05 MUST NOT:
- Modify `docs/DOC-02-final-report.tex` — the main file is frozen after this plan.
- Reorder the `\input{}` chain.
- Add new `\input{}` lines to the main file (if a new section is needed, add it to the appendix).

Plans 04 and 05 MAY:
- Create/modify any `docs/sections/NN-*.tex` file they own per the assignment above.
- `\cite{}` against any of the 6 bibliography keys (zhang2024sieve, yang2023fifo,
  waldspurger2015shards, einziger2017tinylfu, caffeine2024, clauset2009powerlaw).
- Cross-reference `\ref{fig:opening}` (the page-1 MRC figure in the main file).

## The .PHONY Ownership Boundary — What Plan 06-02 Must Honor

Per revision B-1, Plan 06-01 (this plan) is the **exclusive editor** of
the Makefile `.PHONY:` line. In Task 3 above, both `paper` AND `demo`
tokens were added in a single atomic edit. The current line 13 reads:

```make
.PHONY: all clean run run-sweep plots test shards-large phase-04 ablation-s3fifo ablation-sieve ablation-doorkeeper paper demo
```

Plan 06-02 MUST NOT edit this line. Plan 06-02's only Makefile change
is to append the `demo:` target *body* (recipe lines) after the `paper:`
target block at the end of the Makefile. The file grows append-only, and
Plan 01 + Plan 02 never contend on the same byte range. This eliminates
the Wave-1 race the original plan structure had.

## Deviations from Plan

**None for Tasks 1 and 2.** The plan's verbatim snippets (06-PATTERNS.md)
were adopted as-is for the preamble, title block, figure-inclusion block,
and bibliography entries. The intro prose was written fresh but hews
closely to the D-01/D-04 narrative guidance in 06-CONTEXT.md.

**Task 3 — one soft deviation documented below:**

### [Rule 2 — Auto-added documentary redundancy] Explicit `docs/*.aux` entries despite existing `*.aux` coverage

- **Found during:** Task 3 gitignore edit
- **Issue:** The plan's automated acceptance criteria required `grep -q '^docs/\\*\\.aux' .gitignore` etc., but the existing `.gitignore` already covers these via the top-level `*.aux` glob (line 51). Strictly speaking, the explicit `docs/` entries are redundant.
- **Fix:** Added the explicit entries anyway. Rationale: (a) satisfies the plan's acceptance grep, (b) documents Phase 6 intent for future readers, (c) keeps a stable landing site for any future docs/-specific refinements (e.g., pin a single `docs/DOC-02-final-report.pdf` re-include at submission time without having to disentangle it from the global `*.aux` block).
- **Files modified:** `.gitignore` (+10 lines, one new block after the existing LaTeX-artifact block).
- **Commit:** `e87bf91`

## Authentication Gates

None. This plan authored source files only; no network access, no CLI tools
requiring login.

## make paper Behavior After This Plan

Running `make paper` right now will **fail** — the `\input{}` chain
references section files that Plans 04 + 05 have not yet authored.
This is the interface-first contract: the build recipe and main file
are stable, but compilation only succeeds once Wave 2 populates the
bodies (including `sections/10-appendix.tex` per D-11).

After Plans 04 + 05 complete, `make paper` is expected to:
1. `cd docs`
2. Invoke `latexmk -pdf DOC-02-final-report.tex`
3. Produce `docs/DOC-02-final-report.pdf` (gitignored by default;
   uncomment the `.gitignore` line to commit at submission time).

## Known Stubs

None relevant to this plan's deliverables. The `\input{sections/NN-*}`
references in the main file are **contracts**, not stubs — downstream
plans own the implementations and have explicit task assignments.
The `docs/sections/10-appendix.tex` file does not yet exist; Plan 05
Task 4 (per D-11) is the owner. This is expected per the
interface-first design.

## Threat Flags

No new surface. Per the plan's `<threat_model>`, Phase 6 Plan 01
introduces no trust boundaries (file-authoring only, no runtime data
surface, no parser of untrusted data).

## Files Changed

### Created (4)
- `docs/DOC-02-final-report.tex` — 61 lines (Task 1)
- `docs/sections/.gitkeep` — empty (Task 1)
- `docs/sections/01-intro.tex` — 72 lines, 570 words (Task 2)
- `docs/sections/bibliography.tex` — 15 lines, 6 bibitems (Task 2)

### Modified (2)
- `Makefile` — +10 lines: `.PHONY` extended with `paper` and `demo`; new `paper:` target appended (Task 3)
- `.gitignore` — +10 lines: Phase 6 `docs/` LaTeX-artifact block (Task 3)

## Commits

| Task | Hash      | Subject                                                                            |
|------|-----------|------------------------------------------------------------------------------------|
| 1    | `e45475a` | feat(06-01): add docs/DOC-02-final-report.tex scaffold with 11 input stubs         |
| 2    | `a2a033e` | feat(06-01): add intro section and bibliography                                    |
| 3    | `e87bf91` | feat(06-01): add make paper target + .PHONY ownership + docs/ LaTeX gitignore      |

## Self-Check: PASSED

- All 4 created files FOUND in the worktree filesystem (docs/DOC-02-final-report.tex, docs/sections/.gitkeep, docs/sections/01-intro.tex, docs/sections/bibliography.tex).
- All 3 commits FOUND in `git log` (e45475a, a2a033e, e87bf91).
- SUMMARY.md exists at `.planning/phases/06-writeup-demo/06-01-SUMMARY.md`.
- Automated acceptance checks from all 3 tasks pass (11 \input{} stubs; appendix before bibliography; 6 bibitems; .PHONY owns `paper`+`demo`; `make -n paper` emits latexmk; docs/*.aux/*.fdb_latexmk/*.synctex.gz all present in .gitignore).
