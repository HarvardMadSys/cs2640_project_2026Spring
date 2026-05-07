# AI Usage Report

**Project:** On Scheduling and KV-Cache Management for AI Agents & HyperAgenting
**Author:** Milad Rezaei Hajidehi
**Course:** CS 264: Storage Systems, Harvard University

## How I Used AI

AI was the primary coding tool throughout this project; every module was either drafted or substantially edited by an LLM. I used Claude Code (Anthropic's CLI agent, backed by Claude Opus and Sonnet) as my main development partner. GitHub Copilot handled routine autocompletion inside VS Code. The project itself is about serving LLM agents, so the tool and the subject overlapped in ways that were both productive and occasionally disorienting.

Claude Code wrote the first drafts of the ReAct agent loop, the trace collector, the vLLM engine wrapper, the HyperAgent scheduler, and all benchmark harnesses. My role was architecture decisions, prompt engineering for the coding agent, reviewing generated code, debugging runtime failures, and designing experiments. A typical cycle looked like: I described what I wanted in natural language, Claude Code produced a working module, I ran it, hit an error (usually a vLLM API mismatch or an async race condition), pasted the traceback back, and Claude Code fixed it. For the HyperAgent scheduler, this loop took three iterations to get the partner state machine right. For the tool-call parser, it took over ten iterations because Qwen's output format kept surprising us with edge cases.

## What I Learned

**AI can make dangerous assumptions, and I learned to read every response carefully.** Early in the project, I trusted Claude Code's output after a quick glance. This led to two serious bugs that looked correct in code review but were only caught by examining traces:

1. **The SWE-Bench agent was modifying test files instead of source files.** It appeared to "fix" bugs by weakening assertions. The code passed all checks Claude Code suggested, and Claude Code itself found no issue when asked to review. 

2. **Brave Search API rate limits were being hit silently.** The API returned empty results instead of an error, so the RAG agent concluded there was no information available and gave a plausible-sounding but wrong answer. Again, the code looked correct; only the trace showed zero search results on queries that should have returned many.

After these incidents, I added a rule to my Claude Code configuration file (CLAUDE.md): "Always ask questions if something is unclear. We are doing research and it is a big task. Don't worry to ask questions." This forced the AI to surface uncertainty rather than making silent assumptions, and it measurably reduced the rate of subtle bugs in subsequent modules.

**LLM coding agents excel at boilerplate but struggle with stateful concurrency.** The entire trace infrastructure (tracer.py, about 200 lines) was generated in one shot and needed only minor edits. But the async engine integration required me to read vLLM source code myself and explain the internal scheduler API to Claude Code before it could produce correct wrappers. Prompt specificity matters enormously: vague requests like "make the engine async" produced broken code, while precise requests like "wrap AsyncLLMEngine.generate as an async generator, yield RequestOutput, and extract metrics.first_token_time" worked on the first try.

## What I Did Not Expect

The biggest surprise was how much time I spent debugging AI-generated code that looked correct but had subtle concurrency bugs. The HyperAgent worker originally awaited both partners' futures with `asyncio.gather`, which deadlocked when both partners needed the engine simultaneously. Claude Code confidently produced this code and confidently defended it when I questioned it. I had to draw the state machine on paper, identify the deadlock, and then tell Claude Code exactly what to replace it with (`asyncio.wait` with `FIRST_COMPLETED`). The lesson is that AI coding agents are not yet reliable for concurrent control flow; they generate plausible-looking code that passes superficial review but fails under contention.

## Maintaining Context Across Sessions with next_session.md

One practice that proved essential was maintaining a `next_session.md` file at the project root. Claude Code sessions have limited context windows, and this project ran across many sessions over several weeks. At the end of each session, I wrote (or had Claude Code write) a structured handoff document that the next session could read to get up to speed immediately. The file included:

- **What was done this session.** Concrete deliverables: which modules were written, which experiments were submitted, which bugs were fixed.
- **What is pending.** SLURM job IDs, output paths, expected results. For example, after submitting the A100 sweep (N=8 through N=256), the file listed all six job IDs, the exact output paths for `batch_summary.json` and `engine_trace.json`, and shell commands to check job status.
- **A morning checklist.** Step-by-step instructions for the next session: verify jobs finished, read sweep results, identify the breakpoint where KV pool saturates, decide next experiment. This was not prose; it was copy-pasteable shell commands with `python3 -c` one-liners to parse JSON results.
- **Decision tree for next steps.** "If the breakpoint isn't reached at N=256, shrink gpu_memory_utilization. If tasks are too short, raise the tool-call cap." This forced me to think ahead while the context was fresh, rather than re-derive the reasoning next session.
- **Carryover bugs.** Open issues that were not fixed this session but should not be forgotten: `gold_answer` dropped from traces, `python_exec` pandas path bug, dead code in `agent/loop.py`.
- **File references.** Exact file paths and line numbers for code that was changed or is relevant. This saved the next session from searching the codebase.

This file turned out to be the single most valuable artifact for working with AI across sessions. Without it, each new session started with Claude Code asking what the project does and what was last done. With it, I could paste `next_session.md` into the context and the AI was immediately productive. The overhead of writing it (5 minutes at the end of a session) saved 15-20 minutes of re-orientation at the start of the next one.

I also maintained a `CLAUDE.md` configuration file with persistent rules for the AI: coding style, communication preferences, what not to touch, and explicit instructions like "solve things step by step" and "never do what you have not been asked to do." This file was automatically loaded by Claude Code at session start, so the AI's behavior was consistent across sessions without repeating instructions.

## Project-Specific AI Experiences

**Benchmark curation was surprisingly effective with AI.** I gave Claude Code the benchmark APIs (HotpotQA, BrowseComp, GAIA) and asked it to build a curated pool of 512 tasks with balanced representation. It wrote `build_sweep512.py` in one pass: loading from each source, deduplicating, stratifying by difficulty level, and serializing to JSON with a fixed random seed for reproducibility. This is exactly the kind of mechanical but fiddly task where AI saves real time.

**SLURM job scripting was another strength.** Claude Code generated the sweep submission scripts (`submit_sweep.sh`, `run_a100_one_n.sh`) that parameterized concurrency via environment variables, set up the correct conda environment, and handled output directory creation. I ran these on the Harvard SEAS cluster without modification.

**vLLM internals were the hardest domain for AI.** vLLM's codebase is large, fast-moving, and full of version-specific APIs. Claude Code frequently guessed wrong about which class to subclass or which method signature to use, because its training data contained multiple vLLM versions. I had to read vLLM source myself, identify the correct API for version 0.7.3, and paste the relevant source files into Claude Code's context before it could produce working code. The MIG workaround is a good example: Claude Code had no idea that `CUDA_VISIBLE_DEVICES` could contain a MIG UUID string, because this is a niche deployment detail. I found the crash, read the vLLM platform detection code, and told Claude Code exactly what to patch.


