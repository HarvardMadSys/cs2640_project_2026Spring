# Phase 6: Writeup & Demo - Pattern Map

**Mapped:** 2026-04-21
**Files analyzed:** 7 new/modified artifacts
**Analogs found:** 6 / 7 (one fresh-author; strong shape constraints still apply)

This phase is **composition + scripting over existing artifacts** — no simulator
code, no plot code, no analysis code is produced. The patterns below describe
how existing repo conventions constrain the *shape* of each new file, and where
to copy concrete idioms from.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `docs/DOC-02-final-report.tex` (or `.md`) — paper source | markdown-doc / LaTeX-doc | transform (analysis md + figures → PDF) | `progress.tex` (midpoint report, 114 lines, same author/class) + `v7.tex` (proposal, 93 lines) | **exact** — same course, same author, same LaTeX preamble conventions |
| `docs/DOC-02-final-report.pdf` — rendered paper | rendered-artifact | build output | `progress.pdf` (produced via `latexmk` from `progress.tex`; build artifacts `progress.aux/fdb_latexmk/fls/log/out` already in repo root) | exact (build recipe) |
| `docs/DOC-03-ai-use-report.md` — AI-use report | markdown-doc / decision-log | transform (PROCESS.md + planning/* → narrative) | `PROCESS.md` (232 lines, chronological bug+decision log; §"Use of coding agent" in `progress.tex` lines 107-112 is a prior mini-version) + `05-ANALYSIS.md` YAML-front-matter + section shape | role-match (new format, existing voice) |
| `demo.sh` — repo-root live demo | shell-script | batch / orchestration (sources .env, invokes ./cache_sim + make plots, tees to tty) | Makefile `ablation-s3fifo` target (lines 154-167, two-phase invocation + echo banners) + Makefile `plots` target (lines 70-73, DYLD/PYTHONPATH env pattern) + scripts/collect_trace.py §"export CONGRESS_API_KEY" shell-snippet convention | role-match (no existing `.sh` in repo — fresh; but shape is tightly constrained) |
| `demo-rehearsal.log` — rehearsal evidence | log-evidence | append-only text | `traces/collect.log` (20 lines shown; `[N/TOTAL] Ns elapsed` format) + `results/court/collection_progress.log` (same progress-line format) | exact (log shape convention) |
| `traces/demo_trace.csv` — ~5K-request demo trace | trace-slice-data | file-I/O (slice of congress_trace.csv) | `traces/congress_trace.csv` (20,693 lines; CSV header `timestamp,key,size`) + `traces/test_trace.csv` (11 lines, proven minimal slice) | exact (identical schema) |
| `README.md` (MODIFIED) — add Phase 6 quickstart | markdown-doc / repo-meta | transform | existing `README.md` (124 lines, established voice) | exact (intra-file) |
| `docs/demo-backup.mov` — screen-recording backup | binary-artifact | (not code) | none (binary) | n/a — gitignore it if >~10MB per PROCESS.md convention |

---

## Pattern Assignments

### `docs/DOC-02-final-report.tex` (or `.md`) — paper source

**Primary analog:** `progress.tex` (lines 1-30 preamble + header; lines 46-98 results/SHARDS/workload blocks).
**Secondary analog:** `v7.tex` (lines 1-30 preamble + title; lines 74-92 two-column bibliography).

**Format decision (D-17):** LaTeX is already working on this laptop (`progress.pdf` is present, `progress.fdb_latexmk` shows `latexmk` ran successfully). **Pick LaTeX.** Markdown+pandoc would re-introduce environment friction the repo has already solved.

**Preamble pattern** (copy verbatim from `progress.tex:1-17`):
```latex
\documentclass[11pt,letterpaper]{article}
\usepackage[margin=0.8in,top=0.6in,bottom=0.6in]{geometry}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{enumitem}
\usepackage{amsmath}
\usepackage{microtype}
\usepackage{fancyhdr}
\usepackage{graphicx}    % ADD for DOC-02 figure includes
\usepackage{hyperref}    % present in v7.tex:3; add to DOC-02
\usepackage{multicol}    % present in v7.tex:7; useful for bibliography

\pagestyle{fancy}
\fancyhf{}
\lhead{\small CS 2640: Modern Storage Systems $\cdot$ Spring 2026}
\rhead{\small Final Report}           % CHANGE from "Midpoint Report" / "Final Project Proposal"
\cfoot{\thepage}
```

**Title block pattern** (copy shape from `progress.tex:22-27`):
```latex
\begin{center}
{\large\bfseries Caching Policy Evaluation for Legislative Data APIs:\\[-2pt]
Why W-TinyLFU Wins on Court but Ties SIEVE on Congress}\\[3pt]
{\small Mira Yu \hspace{1.5em} [DATE]}
\end{center}
```

**Figure inclusion pattern** (no existing analog — D-04 requires page-1 figure; use standard LaTeX; cite by PDF path):
```latex
\begin{figure}[t]
\centering
\includegraphics[width=\textwidth]{../results/compare/figures/compare_mrc_2panel.pdf}
\caption{Miss-ratio curves for six policies on Congress (left) and Court
(right). Shaded bands are $\pm 1\sigma$ across 5 seeds.
\textbf{Note the 4-5pp gap between W-TinyLFU and SIEVE on Court vs.\ the
photo-finish on Congress at high $\alpha$ — this paper explains why.}}
\label{fig:opening}
\end{figure}
```

**Tabular pattern** (copy from `progress.tex:49-62` — `tabularx` + `booktabs` + `\renewcommand{\arraystretch}{0.92}` + `\footnotesize`):
```latex
{\footnotesize
\renewcommand{\arraystretch}{0.92}
\begin{tabularx}{\textwidth}{@{}l*{5}{X}@{}}
\toprule
\textbf{Cache \%} & \textbf{LRU} & \textbf{FIFO} & \textbf{CLOCK} & \textbf{S3-FIFO} & \textbf{SIEVE} \\
\midrule
...
\bottomrule
\end{tabularx}
}
```

**Bibliography pattern** (copy from `v7.tex:74-92` — two-column `thebibliography`):
```latex
\begingroup
\footnotesize
\setlength{\parskip}{0pt}
\begin{multicols}{2}
\begin{thebibliography}{99}
\setlength{\itemsep}{0pt}\setlength{\parsep}{0pt}
\bibitem{zhang2024sieve} Y.~Zhang et al., SIEVE is Simpler than LRU, \emph{NSDI} '24.
\bibitem{yang2023fifo}   J.~Yang et al., FIFO Queues are All You Need, \emph{SOSP} '23.
\bibitem{waldspurger2015shards} C.~Waldspurger et al., SHARDS, \emph{FAST} '15.
\bibitem{einziger2017tinylfu}  G.~Einziger et al., TinyLFU, \emph{ACM ToS} '17.
\bibitem{caffeine2024}   B.~Manes, Caffeine v3.1.8 reference implementation.
\bibitem{clauset2009powerlaw} A.~Clauset et al., Power-Law Distributions in Empirical Data, \emph{SIAM Rev} '09.
\end{thebibliography}
\end{multicols}
\endgroup
```

**Content sourcing — not a pattern, a constraint:**
- Results tables: pull numbers directly from `results/compare/winner_per_regime.md` (13 lines) and `results/compare/workload_characterization.md` (16 lines). Both are already in markdown-table shape; translate to LaTeX `tabularx`.
- Analysis prose: lightly adapt from `05-ANALYSIS.md` (473 lines) — especially §"Why W-TinyLFU Wins 11/12 MRC Cells" (lines 71-95) and §"Why SIEVE Ties W-TinyLFU on Congress (and Loses on Court)" (lines 98-130+).
- AI-use section (if inline, per D-09): shrink `progress.tex:107-112` pattern; longer version lives in DOC-03.

**Build command** (Makefile does not currently have a `paper` target — either add one or invoke directly):
```bash
cd docs && latexmk -pdf DOC-02-final-report.tex
# OR add a Makefile target:
paper:
    cd docs && latexmk -pdf DOC-02-final-report.tex
```

Build artifacts (`*.aux, *.fdb_latexmk, *.fls, *.log, *.out`) are already gitignored per `.gitignore:51-55`.

---

### `docs/DOC-03-ai-use-report.md` — AI-use report

**Primary analog:** `PROCESS.md` (232 lines, chronological log with H2 phase headers, bug tables under H3 "Round N (code review)" — especially lines 146-170).
**Secondary analog:** `progress.tex:107-112` — prior "Use of coding agent" paragraph (voice reference, honest-but-careful register).
**Tertiary analog:** `05-ANALYSIS.md:1-6` — YAML front-matter pattern for produced-by-Claude documents.

**Front-matter pattern** (copy from `05-ANALYSIS.md:1-6`):
```markdown
---
phase: 06-writeup-demo
produced: 2026-04-21
role: ai-use-report
requirement: DOC-03
---

# AI-Use Report — Caching Policy Simulator

*CS 2640, Spring 2026 — Final Submission*
```

**Section shape** (copy H2/H3 cadence from `PROCESS.md`):
```markdown
## Phase 1: Initial Prototype           # Chronological phase headers like PROCESS.md:9
## Phase 2: Restructuring               # PROCESS.md:29
## Phase 3: New Implementations         # PROCESS.md:58
## Phase 4: Bugs Found and Fixed        # PROCESS.md:143

### Round 1 (code review)               # PROCESS.md:146 — bug-table subsections
| Bug | Severity | Description | Fix |
|-----|----------|-------------|-----|
```

**Bug-table pattern** (copy verbatim column shape from `PROCESS.md:148-152`):
```markdown
| Bug | Severity | Description | Fix |
|-----|----------|-------------|-----|
| MRC units | Critical | Stack distances (object count) compared against cache sizes (bytes) | Pass `ws.unique_objects` instead of `total_bytes / 10` |
```

**Voice reference** (`progress.tex:108-112` — copy the register, not the content):
> "My role was the project design — choosing the [...], deciding on [...], planning the [...]. The agent wrote the code from those specifications. Where it worked well: [...]. Where it needed correction: [...]"

**Bug-count audit (D-07):** The actual count across `PROCESS.md`:
- Round 1: 4 bugs (`PROCESS.md:148-152`)
- Round 2: 4 bugs (`PROCESS.md:158-162`)
- SHARDS denominator (standalone): 1 bug (`PROCESS.md:164-165`)
- = **9 in PROCESS.md body** (matches the original "9-bug list" heuristic)
- PLUS phase-specific deviations in `.planning/phases/*/XX-SUMMARY.md` and `.planning/phases/05-*/05-REVIEW.md` (2 warnings + 10 info — cite by count, not by enumeration)

Plan should include an "audit the number explicitly" step: re-count at write-time, quote the number, link to the source tables.

**Scope expansion (D-09, D-10):** PROCESS.md only covers Phases 1-3 (as of 2026-04-21 it predates Phases 4-5-6). DOC-03 must *extend* the log forward through:
- Phase 4 SHARDS validation + 3 ablation axes (see `.planning/phases/04-*/04-*-SUMMARY.md`)
- Phase 5 analysis infrastructure (see `.planning/phases/05-*/05-REVIEW.md` — "what review caught")
- Meta-layer: GSD planning/orchestration, subagent delegation (research/planner/pattern/reviewer), parallel worktree execution. No existing analog in repo — author fresh, framed as decision-log per D-06.

---

### `demo.sh` — repo-root live demo

**No existing shell script in the repo.** Search confirmed: `Glob("**/*.sh")` returned zero results. Fresh-author, but the *shape* is constrained by four existing conventions.

**Constraint 1 — env sourcing (D-17):** `.env` uses `export FOO=bar` syntax (verified: `export CONGRESS_API_KEY=...` and `export COURTLISTENER_API_KEY=...`). Native `source .env` works; no `python-dotenv` needed.

```bash
#!/usr/bin/env bash
# demo.sh — DOC-04 live 6-policy sweep on pre-generated 5K-request trace.
# Rehearsed 3x on target laptop per D-18; see demo-rehearsal.log.

set -euo pipefail

# --- env setup (D-17) ---
# .env defines: CONGRESS_API_KEY, COURTLISTENER_API_KEY (not strictly needed
# for demo, but harmless); plus anything else the Phase 1-3 workflow established.
if [[ -f .env ]]; then
  # shellcheck source=/dev/null
  source .env
fi

# macOS libexpat workaround — load-bearing, copied from Makefile:63-72.
export DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib
export PYTHONPATH=.venv/lib/python3.14/site-packages
PLOT_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14
```

**Constraint 2 — invocation shape (Makefile analog):** copy the *banner + sequential-invocation* idiom from Makefile `ablation-s3fifo` target (lines 154-167):

```bash
# --- step banner pattern (copied from Makefile:156, 161) ---
echo "=== Step 1/3: building cache_sim ==="
make --quiet

echo ""
echo "=== Step 2/3: 6-policy sweep on 5K Congress slice (Congress α_raw=0.23) ==="
./cache_sim --trace traces/demo_trace.csv --replay-zipf \
            --policies lru,fifo,clock,s3fifo,sieve,wtinylfu \
            --cache-sizes 0.001,0.01,0.05,0.1 \
            --output-dir results/demo

echo ""
echo "=== Step 3/3: rendering live figure ==="
DYLD_LIBRARY_PATH="$DYLD_LIBRARY_PATH" PYTHONPATH="$PYTHONPATH" \
  "$PLOT_PYTHON" scripts/plot_results.py --workload demo

echo "=== demo complete. Figure: results/demo/figures/mrc.pdf ==="
```

**Constraint 3 — miss-ratio live-print (D-15 narration):** After `./cache_sim` writes `results/demo/mrc.csv`, pretty-print it with `column -t -s,` for the audience. Matches PROCESS.md `traces/collect.log` progress-line aesthetic (`[N/TOTAL] ... ` banner lines).

```bash
echo ""
echo "=== Miss-ratio table (live) ==="
column -t -s, results/demo/mrc.csv
```

**Constraint 4 — rehearsal wall-clock capture (D-18):** The script must self-report timing so `demo-rehearsal.log` accumulates concrete evidence. Use `time` wrapper or explicit `date +%s` deltas (no existing repo analog, but standard shell idiom).

```bash
START=$(date +%s)
# ... demo body ...
END=$(date +%s)
echo "=== wall-clock: $((END - START))s ==="
```

**Gitignore note:** `results/**` is gitignored per `.gitignore:33` with specific re-includes. `results/demo/` will be regenerated on each `demo.sh` run — no new re-include needed.

---

### `demo-rehearsal.log` — rehearsal evidence

**Primary analog:** `traces/collect.log` — plain text, progress-style, append-only. Format:
```
Collecting 10000 requests (max 36000s), appending to /Users/mirayu/civicache/traces/congress_trace.csv
API key: mLgdlNVv...
  [100/10000] 194s elapsed, last: bill/119/hconres/6874 (231 bytes, HTTP 404)
```

**Secondary analog:** `results/court/collection_progress.log` — same pattern (`[N/TOTAL] Ns elapsed — last: ...`).

**Recommended shape** (not a verbatim copy — structure per D-18 "3 rehearsals logged"):
```
=== Rehearsal 1 — 2026-04-22 14:05 ===
[invocation]  ./demo.sh
[wall-clock]  47s
[stdout tail] === demo complete. Figure: results/demo/figures/mrc.pdf ===
[verdict]     PASS

=== Rehearsal 2 — 2026-04-22 14:12 ===
...

=== Rehearsal 3 — 2026-04-22 14:19 ===
...
```

**Capture idiom:** `./demo.sh 2>&1 | tee -a demo-rehearsal.log` — standard shell pattern, no existing script-based analog but matches how `traces/collect.log` was produced (the collector scripts `print(...)` to stdout; `nohup` redirects to `collect.log` per `PROCESS.md:108`).

**Committed:** yes. Per D-18 "commit the log as evidence." Note: the file is NOT currently gitignored (`.gitignore` has no `*.log` entry at repo root — only `progress.log` is specifically ignored on line 55). Confirm at planning time.

---

### `traces/demo_trace.csv` — ~5K-request pre-generated demo trace

**Primary analog:** `traces/congress_trace.csv` (20,693 lines total). Header + first rows:
```
timestamp,key,size
1775967571591,amendment/119/samdt/1004,1665
1775967573251,bill/114/s/4838,225
```

**Secondary analog:** `traces/test_trace.csv` (11 lines) — proves the simulator accepts tiny trace slices.

**Generation pattern (D-19 — Claude's discretion, first-5K verbatim OR seeded sub-sample):**

*Option A — verbatim first-5K (simpler, deterministic):*
```bash
head -1 traces/congress_trace.csv > traces/demo_trace.csv            # header
tail -n +2 traces/congress_trace.csv | head -n 5000 >> traces/demo_trace.csv
```

*Option B — seeded uniform sample (preserves distributional shape better):*
```bash
# shuf with fixed seed for determinism
head -1 traces/congress_trace.csv > traces/demo_trace.csv
tail -n +2 traces/congress_trace.csv | shuf --random-source=/dev/zero -n 5000 >> traces/demo_trace.csv
```

**Gitignore re-include required:** `traces/*` is ignored (`.gitignore:29`), with specific re-includes for `court_trace.csv` and `court_pilot.csv` (`.gitignore:36-37`). Add:
```
!traces/demo_trace.csv
```
…to `.gitignore` OR alternatively generate demo_trace.csv at the top of `demo.sh` every run (no commit needed). Planning-time decision.

---

### `README.md` (MODIFIED) — add Phase 6 quickstart

**Primary analog:** `README.md` itself (124 lines, H2 sections, triple-backtick code fences, table of policies at lines 9-15).

**Additions to match existing voice:**
- Update H2 "Policies" table (lines 9-15) to include **W-TinyLFU** as 6th row (currently only lists 5 policies — but Phase 2 added W-TinyLFU).
- Add H3 "Live demo" under "Run" (lines 25-58):
  ```markdown
  ### Live demo (60s, 6-policy sweep)

  ```bash
  ./demo.sh
  ```

  Runs `./cache_sim` on a pre-generated 5K-request slice of the Congress trace
  with all 6 policies at 4 cache sizes, prints the miss-ratio table, and
  renders `results/demo/figures/mrc.pdf`. See `demo-rehearsal.log` for
  timing evidence.
  ```
- Add to "Project Structure" tree at lines 99-117: `demo.sh`, `docs/`.
- Update "References" section (lines 119-124) to match DOC-02's bibliography (add Einziger TinyLFU, Caffeine).

---

## Shared Patterns

### Env-var setup (applies to demo.sh and any Python invocation Phase 6 triggers)

**Source:** `Makefile:63-73`
**Apply to:** `demo.sh`

```make
# macOS libexpat workaround: Python 3.14's pyexpat requires a newer libexpat
# than /usr/lib/libexpat.1.dylib ships.
PLOT_PYTHON     ?= /opt/homebrew/opt/python@3.14/bin/python3.14
PLOT_PYTHONPATH ?= .venv/lib/python3.14/site-packages

plots:
	DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib \
	PYTHONPATH=$(PLOT_PYTHONPATH) \
	$(PLOT_PYTHON) scripts/plot_results.py --workload $(WORKLOAD)
```

**Shell translation (for demo.sh):**
```bash
export DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib
export PYTHONPATH=.venv/lib/python3.14/site-packages
PLOT_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14
```

### Banner-framed sequential steps (applies to demo.sh + DOC-02 methodology narrative)

**Source:** Makefile `ablation-s3fifo` target, lines 154-167 and `shards-large` target, lines 132-146
**Apply to:** `demo.sh` step orchestration

```make
@echo "=== Step 1/2: 50K oracle regime (--limit 50000 + --shards-exact) ==="
./$(TARGET) --trace traces/shards_large.csv --shards --shards-exact ...
@echo ""
@echo "=== Step 2/2: 1M self-convergence regime ==="
./$(TARGET) --trace traces/shards_large.csv --shards ...
```

### Figure/table citation style (applies to DOC-02 + DOC-03)

**Source:** `progress.tex:49-62` + `05-ANALYSIS.md:44-52`
**Apply to:** Every table in DOC-02, every bug/decision cross-reference in DOC-03

- Tables: `booktabs` (`\toprule`, `\midrule`, `\bottomrule`) + `tabularx` for column widths + `\footnotesize` + `\renewcommand{\arraystretch}{0.92}`.
- Figures: `\includegraphics[width=\textwidth]{../results/.../fig.pdf}` with paths relative to `docs/` subdirectory.
- Cross-references: `\label{fig:X}` + `\ref{fig:X}` (`progress.tex` doesn't use these — but the repo is otherwise bibtex-free, so manual `\bibitem` keys suffice).

### Chronological-log H2/H3 structure (applies to DOC-03)

**Source:** `PROCESS.md` entire document
**Apply to:** `docs/DOC-03-ai-use-report.md`

- H2 = major phase or theme ("Phase 4: Bugs Found and Fixed", "Use of coding agent").
- H3 = sub-topic within phase ("Round 1 (code review)", "S3-FIFO impact").
- Bug/decision tables: `| Thing | Severity | Description | Fix |` four-column shape.
- Prose between tables: short paragraphs explaining *why* the thing mattered.

---

## No Analog Found

| File | Role | Reason | Mitigation |
|------|------|--------|------------|
| `demo.sh` | shell-script | No prior `.sh` in repo | Fresh-author, but constrained by Makefile env-var idiom + `.env` sourcing convention (both documented above). |
| `docs/DOC-02-final-report.pdf` (final artifact) | rendered-pdf | LaTeX produces a new binary; the prior `progress.pdf` is gitignored (`.gitignore:74`) | Treat as build artifact — gitignore `docs/*.pdf` OR commit at final submission time only. Planning-time decision. |
| `docs/demo-backup.mov` | binary-video | No prior video in repo | Not code; may be gitignored if >10MB. Include a `.gitignore` entry: `docs/demo-backup.mov` (or `docs/*.mov`). |

---

## Constraints Worth Flagging to Planner

1. **Paper source language:** LaTeX is strongly favored over markdown+pandoc because `progress.tex`+`progress.pdf` already built successfully on this laptop with `latexmk`. Existing build-artifact gitignore entries (`*.aux`, `*.fdb_latexmk`, `*.fls`, `*.synctex.gz`, `progress.log`) already cover LaTeX. Choosing pandoc reintroduces environment risk for zero authoring benefit.

2. **Figures exist; don't regenerate in this phase.** All 11 figures DOC-02 needs are already in `results/*/figures/*.pdf`. The paper sources them by relative path. Figure regeneration is a one-liner (`make plots WORKLOAD=congress && make plots WORKLOAD=court`) but is *not part of Phase 6 scope* — it's a pre-flight check.

3. **Gitignore boundaries are load-bearing:**
   - `PROCESS.md` at repo root is ignored (`.gitignore:72` — `/PROCESS.md`). DOC-03 must live at a path that is NOT ignored. Recommended: `docs/DOC-03-ai-use-report.md`. (Confirm at planning time; user may have intended to commit PROCESS.md separately.)
   - `results/**` is ignored — the paper references figures there but the paper source must NOT embed rendered PDF pages in git; instead, cite by path and rely on `make plots` regeneration.
   - `.env` is ignored — do not commit a copy into `demo.sh`.

4. **PROCESS.md bug-count (D-07):** Current count is 9 in the body (4+4+1). DOC-03 should re-audit at write-time and include the number explicitly with the breakdown. Do not pin to "9" in the plan — use whatever the audit returns.

5. **Demo trace placement:** If committed, it needs an affirmative `.gitignore` re-include (`!traces/demo_trace.csv`), matching the pattern for `court_trace.csv` (`.gitignore:36`). If generated at runtime by `demo.sh`, no gitignore change is needed. Either is defensible — document the choice in the plan.

---

## Metadata

**Analog search scope:**
- `/Users/mirayu/civicache/` (repo root) — Makefile, README.md, PROCESS.md, progress.tex, v7.tex, .env, .gitignore
- `/Users/mirayu/civicache/scripts/` — all 9 Python scripts scanned for env-var + CLI patterns
- `/Users/mirayu/civicache/.planning/` — ROADMAP, REQUIREMENTS, PROJECT, STATE, all phase directories
- `/Users/mirayu/civicache/results/compare/` — workload_characterization.md, winner_per_regime.md, figures/*.pdf
- `/Users/mirayu/civicache/traces/` — CSV schema + existing log formats
- `/Users/mirayu/civicache/.planning/phases/05-*/05-ANALYSIS.md` — prose source for paper analysis section

**Files scanned (read):** 9 (CONTEXT.md, Makefile, README.md, PROCESS.md, progress.tex, v7.tex, 05-ANALYSIS.md [partial], workload_characterization.md, winner_per_regime.md). Plus directory listings and grep scans across ~30 additional files.

**Pattern extraction date:** 2026-04-21
