# AI Usage Statement

This project (CS 2640, Spring 2026) made heavy use of AI assistance.
This document is a candid account of how, where, and to what extent.
Ironically, the document itself was drafted by Claude — but reviewed
and edited by me.

## Tool

**Claude Code** (Anthropic), primarily the Opus 4.6 and Opus 4.7 models,
run as the interactive CLI in the project working directory. Sessions
spanned roughly the full project window. No other AI tools (ChatGPT,
Copilot, Cursor, Gemini, etc.) were used in the loop.

## How it was used

The project was developed in a tight human-in-the-loop pattern. I (Minkai)
drove direction, decisions, and verification; Claude did most of the
typing, much of the literature recall, and a substantial share of the
analysis framing. Concretely:

- **Planning and literature review.** `ATTACK_PLAN.md` was written
  collaboratively with Claude across many sessions and is explicitly
  structured as a hand-off document for future Claude instances. The
  ranked attack ideas, related-work pointers, and trace inventory in
  §1–§7 are AI-drafted from prompts I refined. I collaborated with
  Claude to decide on reasonable next steps at each stage.
- **Implementation.** Most files under `plugins/` (the experimental
  harness, baseline reimplementations, V4 learned-promotion gate,
  EXP3 sizer, decision-stump admission, diagnostic scripts,
  cachesim subprocess wrapper, trace converters) were drafted by
  Claude from my specifications and then iterated on. I ran the
  code, debugged failures together with Claude, and made the
  algorithmic choices (feature set, label horizon, learning-rate
  schedule, ablation structure).
- **Experimentation.** Claude was useful for summarizing the
  experiments and parsing per-cell results into the §8–§15 tables.
  In practice, I also found it valuable to look at results myself
  to come up with future directions, rather than relying solely on
  Claude's summaries.
- **Diagnostics.** The §15 weight-jitter / label-imbalance analysis
  was a Claude-suggested direction after I asked why V4+OptS wasn't
  beating OptS_T2; the diagnostic harness (`plugins/diag_v4.py`) and
  the pathology-to-cell mapping were co-developed.
- **Writing.** Claude wrote an initial draft of `final_report.tex`
  from `ATTACK_PLAN.md`. I then extensively edited the prose and
  fact-checked every number against the underlying experimental
  results. References and BibTeX entries were drafted by Claude
  and verified by me against the actual papers.

## Honest assessment

Claude accelerated this process, most significantly in implementing
experiments but also for helping in idea generation. However, I still
found it critical to review experiments myself to decide next steps
and to set the high-level framing of this project.
