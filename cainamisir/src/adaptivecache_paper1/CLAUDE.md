# AdaptiveCache Research Wiki — Operational Schema

This file governs how this wiki is structured and maintained. Read it at the start of every session.

## Project

**AdaptiveCache** is a research project by Vlad Cainamisir (Harvard University) proposing a live context management system for LLM agents. The core idea: model context as a live, ordered memory that must be continuously optimized for prefix cache reuse. The system jointly performs selection, eviction, and reordering — placing stable, high-value items early to maximize KV-cache hit rate, while demoting volatile items toward the suffix for eviction. See `wiki/overview.md` for the full project description and evolving thesis.

## Directory Structure

```
raw/                        # Immutable source documents — never modify
  papers/                   # Academic papers (PDF or markdown)
  proposal.txt              # AdaptiveCache project proposal
wiki/                       # LLM-maintained knowledge base — I write, you read
  index.md                  # Master content index (update on every ingest)
  log.md                    # Append-only activity log
  overview.md               # Project overview and evolving thesis
  <concept>.md              # Technical concept pages
  <paper-slug>.md           # Source summary pages
```

## Page Format

Every wiki page uses this YAML frontmatter:

```yaml
---
title: Page Title
type: concept | source | overview | synthesis
tags: [tag1, tag2]
sources: [paper-slug]        # for concept/synthesis pages: which sources informed this
date_created: YYYY-MM-DD
date_updated: YYYY-MM-DD
---
```

## Operations

### Ingest

When told to ingest a source in `raw/`:

1. Read the source
2. Discuss key takeaways with the user (skip in batch mode)
3. Create `wiki/<slug>.md` — source summary page
4. Update `wiki/index.md` with the new entry
5. Create or update relevant concept pages, cross-referencing the source
6. Update `wiki/overview.md` if the source affects the project thesis or competitive landscape
7. Append to `wiki/log.md`: `## [YYYY-MM-DD] ingest | <Title>`

A single source typically touches 5–15 wiki pages. Flag contradictions with existing claims explicitly. Flag direct relevance to AdaptiveCache explicitly.

### Query

When answering a research question:

1. Read `wiki/index.md` to identify relevant pages
2. Read the relevant pages and synthesize an answer with citations
3. Offer to file valuable answers (comparisons, analyses, novel connections) as new wiki pages
4. Append to `wiki/log.md`: `## [YYYY-MM-DD] query | <question summary>`

### Lint

When asked to lint the wiki:

1. Check for contradictions between pages
2. Find orphan pages (no inbound links)
3. Find concepts mentioned but lacking their own page
4. Check for stale claims superseded by newer sources
5. Suggest new questions and sources to investigate
6. Append to `wiki/log.md`: `## [YYYY-MM-DD] lint | health check`

## Naming Conventions

- Source pages: kebab-case short title — `context-folding.md`, `mem1.md`, `react.md`
- Concept pages: descriptive kebab-case — `prefix-caching.md`, `kv-cache.md`, `context-management.md`
- Cross-links: standard markdown `[text](page.md)` — Obsidian renders these as wikilinks if you prefer `[[page]]`
- Always update `wiki/index.md` when creating new pages

## Key Concepts (seed list)

These are the core technical concepts this wiki tracks. Each should eventually have its own page.

- **prefix caching / KV-cache reuse** — the cost model that motivates AdaptiveCache
- **context management** — the umbrella: eviction, selection, ordering/layout
- **attention signals** — used by AdaptiveCache to identify high-value tokens
- **context folding** — ByteDance approach: branch/return sub-trajectories (see `context-folding.md`)
- **context summarization** — heuristic compression at context limit (baseline to beat)
- **multi-agent context distribution** — distributing context across specialized agents
- **SWE-bench** — primary benchmark for agentic coding evaluation
- **ReAct** — baseline agent framework (reason + act loop)

## AdaptiveCache vs Related Work (running comparison)

| Approach | Mechanism | Training needed | Layout-aware | Prefix-cache-aware |
|---|---|---|---|---|
| AdaptiveCache (proposed) | compaction + reordering, online | No (heuristic) | Yes | Yes |
| Context Folding | branch/return sub-trajectories | Yes (RL) | No | Partially (KV rollback) |
| Summarization-based | post-hoc compression | Optional | No | No |
| Multi-agent | distribute across agents | No | No | No |
| Full context (ReAct) | keep everything | No | No | No |

Update this table as new sources are ingested.
