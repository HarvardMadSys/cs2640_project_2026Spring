---
title: Claude Code Compaction (Anthropic, 2025)
type: source
tags: [context-management, summarization, compaction, production-system, prefix-caching, kv-cache, microcompact, tool-results]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Claude Code Compaction

**Organization:** Anthropic  
**Type:** Production system (Claude Code CLI / IDE agent)  
**Source:** `raw/claude_code_compaction.md` (internal technical reference, `src/services/compact/`)

This is the most detailed production compaction system we have source-level visibility into. It is a **layered pipeline of four distinct strategies** running in fixed order on every query turn, plus reactive fallbacks. Far more sophisticated than the Anthropic API compaction (`compact-2026-01-12`) — that is a single-pass summarization; this is a full compaction architecture.

## Architecture: Four-Stage Pipeline

Every query flows through this fixed sequence before the API call:

```
incoming messages
       │
  [1] SNIP          ← cheapest; removes old history chunks (sliding window)
       │
  [2] MICROCOMPACT  ← no LLM call; clears/cache-edits old tool results
       │
  [3] CONTEXT       ← ant-only; selective collapse of resolved sub-problems
     COLLAPSE
       │
  [4] AUTOCOMPACT   ← last resort; full LLM summarization if still over threshold
       │
   API call ──────── (prompt-too-long error?) → reactive compact fallback
```

Manual `/compact` command bypasses threshold checks and always runs.

## Strategy 1: Snip

Lightest intervention. Removes old blocks of history in chunks — a sliding window dropping the oldest material when the buffer grows large. Runs before microcompact so both can fire on the same turn.

`snipTokensFreed` is passed downstream to autocompact's threshold calculation — critical because the surviving assistant message's `.usage` field still reflects the pre-snip context window, so without this offset, autocompact would see a falsely inflated token count.

## Strategy 2: Microcompact

**Key insight: no LLM call.** Microcompact reduces the payload of tool results — the largest per-turn contributor to context size. Only compactable tools are targeted:

```
FILE_READ, SHELL tools, GREP, GLOB, WEB_SEARCH, WEB_FETCH, FILE_EDIT, FILE_WRITE
```

Two sub-strategies, tried in order:

### 2a. Time-Based Microcompact
If the gap since the last assistant message exceeds a threshold, the server-side prompt cache has certainly expired. Old tool results are replaced in-place with `[Old tool result content cleared]`, keeping only the most recent `keepRecent` results. Cheap and deterministic.

### 2b. Cached Microcompact (Ant-only)
**This is the most interesting sub-strategy for AdaptiveCache.**

When the cache is warm, instead of mutating local messages, it sends `cache_edits` to the API layer — instructions to delete the cached KV state of old tool results **without invalidating the rest of the prefix**. The local message objects are **not modified**. The server surgically removes selected cached entries while leaving the remaining cached prefix intact.

```typescript
// Local messages untouched — cache_edits queued for API layer
return {
  messages,           // ← unchanged
  compactionInfo: {
    pendingCacheEdits: {
      trigger: 'auto',
      deletedToolIds: toolsToDelete,
      baselineCacheDeletedTokens: baseline,
    },
  },
}
```

This is prefix-cache-aware compaction. It answers the question AdaptiveCache cares about: can you remove items from the middle of the context without invalidating the downstream cache? Answer: yes, if the server supports surgical cache edits.

## Strategy 3: Context Collapse (Ant-only)

A more granular alternative to full compaction. Rather than replacing the entire history with a summary, it selectively "collapses" individual sections — resolved sub-problems — via a commit log that is replayed as a projection on every turn. If collapse gets the token count under the autocompact threshold, autocompact is skipped entirely.

This is conceptually close to Context Folding (branch/return) but implemented as a projection layer rather than an agent-level mechanism — the agent doesn't invoke it explicitly.

## Strategy 4: Autocompact

Fires when the token count exceeds:

```
autocompactThreshold = contextWindow − min(maxOutputTokens, 20_000) − 13_000
```

For a 200K context model: `200K − 8K − 13K = 179K tokens`.

Tries two things in order:

1. **Session memory compaction** (cheaper — no LLM call): reuses an already-extracted session memory file. Calculates a "keep slice" working backwards from the last summarized message, expanding until token minimums are met (default: min 10K tokens, min 5 text-block messages, max 40K tokens).

2. **Full LLM compaction** (`compactConversation`): streams a summary via a forked agent that reuses the main conversation's prompt cache prefix. This is the key cost optimization — the fork inherits the parent's cached prefix so only the summarization request itself is sent, not the entire history. Falls back to direct streaming if the fork fails.

## The Summarization Prompt

The summarizer is instructed to produce two XML sections:

```
<analysis>
  [Private scratchpad — chronological walk of every message]
  [Stripped before entering context — only improves summary quality]
</analysis>

<summary>
  1. Primary Request and Intent
  2. Key Technical Concepts
  3. Files and Code Sections (with full code snippets)
  4. Errors and fixes
  5. Problem Solving
  6. All user messages
  7. Pending Tasks
  8. Current Work
  9. Optional Next Step
</summary>
```

Notable: the `<analysis>` scratchpad is stripped before the summary enters context. It's a chain-of-thought that improves the summary but has no informational value once written. Clean design.

Images in user messages are stripped and replaced with `[image]` placeholders before the compact call to avoid the compact request itself hitting prompt-too-long.

## Post-Compact Attachments

After compaction, the model loses context. A set of attachments is re-injected in fixed order:

| Attachment | Limit | Strategy |
|---|---|---|
| Recently accessed files | 5 files, 5K tokens/file, 50K total | Most-recent-first, skip files in kept tail |
| Invoked skills | 5K tokens/skill, 25K total | Most-recent-first, truncate at head |
| Plan file | Full content | Re-inject if exists |
| Deferred tools delta | — | Re-announce tools loaded via ToolSearch |
| MCP instructions delta | — | Re-announce MCP server instructions |

## Circuit Breaker

If autocompact fails `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES` times in a row, it stops firing. Prevents infinite retry loops on pathological inputs. Counter resets on success.

## Partial Compaction

Users can compact only a slice of the conversation:
- `direction: 'up_to'` — summarize everything before a selected message, keep the tail verbatim
- `direction: 'from'` — keep the prefix verbatim, summarize everything after a selected message

The boundary is annotated with `preservedSegment` metadata (`headUuid`, `anchorUuid`, `tailUuid`) for transcript reconstruction.

## Relationship to AdaptiveCache

This is the most technically rich production compaction system we've documented. Key observations:

**What it does that AdaptiveCache doesn't:**
- **Cached microcompact (`cache_edits`)** — surgical server-side deletion of cached tool results without prefix invalidation. This is the one mechanism in any system we've seen that is genuinely prefix-cache-preserving for selective eviction. If AdaptiveCache could use a similar API, the layout problem would be partially solved at the infrastructure level rather than the application level.
- **Forked agent for summarization** — reusing the parent's cached prefix during the compact call itself is elegant; avoids paying double for the history being summarized.
- **Session memory reuse** — avoiding the LLM summarization call entirely when a memory snapshot is available is a strong optimization.
- **Post-compact attachment re-injection** — systematically restoring working state (files, skills, plans) after compaction is operationally important and underappreciated in academic work.

**The structural gap (same as all summarization):**
Full autocompact still generates new summary tokens → new byte sequence → no prefix KV-cache overlap for the conversation portion. The system prompt and the first few turns may be cached, but the dynamic conversation history starts from scratch after every full compaction event.

**Cached microcompact is the exception** — it is the closest thing we've seen to AdaptiveCache's core idea: selectively removing items from the cached context without invalidating the rest. The difference is that it only applies to tool results, it requires a Anthropic-specific API extension (`cache_edits`), and it doesn't reorder what remains. AdaptiveCache generalizes this to arbitrary items with an explicit layout step.

**The pipeline order matters:** Snip → Microcompact → Context Collapse → Autocompact is a cheapest-first hierarchy. AdaptiveCache could adopt the same structure: snip-like eviction of volatile items, then layout optimization of what remains, with full summarization only as a last resort.

**AdaptiveCache's unique contribution vs this system:**
- No new token generation for eviction (vs all four strategies except cached microcompact)
- Explicit layout optimization (vs no layout step in any strategy)
- Works without server-side API extensions (vs cached microcompact requires `cache_edits`)
- Generalizes beyond tool results to arbitrary context items

## Citations / Related Pages

- [anthropic-compaction.md](anthropic-compaction.md) — Anthropic's simpler API-level compaction (`compact-2026-01-12`)
- [context-folding.md](context-folding.md) — Context Collapse (Strategy 3) is functionally similar to Context Folding
- [openhands-condensation.md](openhands-condensation.md) — OpenHands's modular condenser pipeline; similar multi-strategy approach
- [context-management.md](context-management.md) — taxonomy
- [prefix-caching.md](prefix-caching.md) — why cached microcompact is the interesting one
