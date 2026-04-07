---
title: Anthropic Server-Side Compaction — Context Management API (Beta, 2026)
type: source
tags: [context-management, summarization, inference-efficiency, long-context, prefix-caching, agent]
date_created: 2026-04-04
date_updated: 2026-04-04
---

# Anthropic Server-Side Compaction: Context Management API (Beta)

**Provider:** Anthropic  
**Feature name:** Server-side compaction  
**Beta header:** `compact-2026-01-12`  
**API parameter:** `context_management.edits[].type = "compact_20260112"`  
**Supported models:** Claude Opus 4.6 (`claude-opus-4-6`), Claude Sonnet 4.6 (`claude-sonnet-4-6`)  
**Status:** Beta (as of April 2026)  
**Source:** [Anthropic Compaction Docs](https://platform.claude.com/docs/en/build-with-claude/compaction)

## Problem

Long-running agentic conversations and tool-use workflows accumulate context linearly across turns. As conversations grow, two distinct problems emerge:

1. **Context window limits:** Total input tokens can exceed the model's maximum window (200K for Sonnet 4, 1M for Opus/Sonnet 4.6), causing hard failures.
2. **Context rot:** Even before the window limit is hit, long contexts degrade model performance. The model struggles to maintain focus and retrieve relevant information across a very long history — Anthropic explicitly calls this "context rot" in their documentation.

Prior solutions require the developer to manually track token counts, select which messages to truncate or summarize, and re-prompt accordingly. Server-side compaction automates this entire management loop inside the API.

## How Compaction Works

When compaction is enabled, the API automatically summarizes older conversation content when approaching a configurable token threshold:

1. **Trigger detection:** Before each sampling iteration, the API checks if input tokens exceed the configured `trigger` threshold (default: 150,000 tokens; minimum: 50,000 tokens).
2. **Summary generation:** If the threshold is exceeded, Claude generates a summary of the earlier conversation using a default or user-provided summarization prompt. This is an additional sampling pass (a separate LLM inference call).
3. **Compaction block creation:** The API emits a `compaction` block at the start of the assistant response, containing the generated summary.
4. **Continuation:** The response continues as normal after the compaction block.
5. **Subsequent turns:** On the next API call, when the client appends the full response (including the compaction block) to `messages`, the API automatically drops all message blocks prior to the compaction block. The effective conversation history becomes: [compaction summary] + [messages after compaction point].

The system prompt is always preserved across compaction — only the conversation history (messages) is compressed.

**Default summarization prompt:**
```
You have written a partial transcript for the initial task above. Please write a summary of the transcript. The purpose of this summary is to provide continuity so you can continue to make progress towards solving the task in a future context, where the raw history above may not be accessible and will be replaced with this summary. Write down anything that would be helpful, including the state, next steps, learnings etc. You must wrap your summary in a <summary></summary> block.
```

## API Parameters

| Parameter | Type | Default | Description |
|:---|:---|:---|:---|
| `type` | string | Required | Must be `"compact_20260112"` |
| `trigger` | object | 150,000 tokens | `{type: "input_tokens", value: N}`; minimum 50,000 |
| `pause_after_compaction` | boolean | `false` | Stop after generating compaction block, return `stop_reason: "compaction"` |
| `instructions` | string | null | Custom prompt replacing the default; entirely overrides the default |

### Basic Usage (Python)

```python
response = client.beta.messages.create(
    betas=["compact-2026-01-12"],
    model="claude-opus-4-6",
    max_tokens=4096,
    messages=messages,
    context_management={"edits": [{"type": "compact_20260112"}]},
)
# Append entire response (including any compaction block) to continue
messages.append({"role": "assistant", "content": response.content})
```

### Compaction Response Block Format

```json
{
  "content": [
    {
      "type": "compaction",
      "content": "Summary of the conversation: The user requested help building a web scraper..."
    },
    {
      "type": "text",
      "text": "Based on our conversation so far..."
    }
  ]
}
```

### Usage Tracking

Compaction requires an additional sampling step billed separately:

```json
{
  "usage": {
    "input_tokens": 23000,
    "output_tokens": 1000,
    "iterations": [
      {"type": "compaction", "input_tokens": 180000, "output_tokens": 3500},
      {"type": "message", "input_tokens": 23000, "output_tokens": 1000}
    ]
  }
}
```

The top-level `input_tokens` / `output_tokens` fields exclude compaction costs. To calculate total billed tokens, sum across `iterations`. Re-applying a previous `compaction` block (i.e., when no new compaction triggers) does not add any compaction cost.

### Pause Mode

`pause_after_compaction: true` returns `stop_reason: "compaction"` immediately after generating the summary, before generating the actual response. This gives the developer a hook to inject additional content (e.g., preserved recent messages, injected instructions) before the conversation continues. Useful for:
- Enforcing total token budgets across many compaction events
- Preserving specific messages that should survive compaction
- Adding updated task state after each compaction cycle

### Compatibility

Compaction is compatible with:
- **Prompt caching:** System prompts with `cache_control: {type: "ephemeral"}` remain cached across compaction events, because the system prompt is never included in the compacted content. This is explicitly noted as a best practice in the docs.
- **Streaming:** The compaction block streams as a single atomic `content_block_delta` (no intermediate streaming; the summary appears all at once as `compaction_delta`).
- **Server tools (web search):** Compaction trigger is checked at each sampling iteration when server tools are active; multiple compactions may occur within a single request.
- **Token counting API:** The `/v1/messages/count_tokens` endpoint applies existing compaction blocks to compute the effective token count but does not trigger new compactions.

### Compatibility Limitations

Compaction is currently **not supported** in combination with:
- Extended thinking (thinking blocks)
- PDF and file attachments in tool results
- Tool definitions that are updated between turns (tool schema changes between turns)
- Batches API

## Relationship to AdaptiveCache

Anthropic's compaction feature is the direct production-deployed precedent for server-side context management of agent conversations. It is simultaneously the closest competitor to AdaptiveCache in the problem space and the clearest demonstration of the structural gap AdaptiveCache addresses.

**Points of contact:**

- **Production validation of the problem:** Anthropic's deployment of server-side compaction confirms that context management for long-running agents is a real and important infrastructure problem — important enough for Anthropic to build it into the API itself. The explicit mention of "context rot" as a motivation is a direct endorsement of the degraded-performance problem that AdaptiveCache also targets. Compaction's existence validates AdaptiveCache's problem statement.

- **Same target deployment context:** Compaction is explicitly designed for "chat-based, multi-turn conversations" and "task-oriented prompts that require a lot of follow-up work (often tool use)" — exactly the agentic setting AdaptiveCache targets. The 150,000-token default trigger and 50,000-token minimum indicate the scale at which context management becomes necessary in production.

- **Pause mode is AdaptiveCache's hook:** The `pause_after_compaction` flag creates a callback between the summary generation and the response generation. This is architecturally equivalent to AdaptiveCache's inter-step compaction hook — a moment where the serving layer can inspect and modify the effective context before it is used for the next generation. AdaptiveCache's compaction pass runs at exactly this insertion point, but instead of generating a summary, it reorders existing tokens into a layout-optimal stable prefix.

- **Prompt caching + compaction interaction reveals the gap:** Anthropic's own documentation notes that the system prompt remains cached across compaction events and recommends using `cache_control` on system prompts. This is because the system prompt is never modified by compaction. But the conversation history — the part that changes — is replaced by a new summary string on each compaction event, which invalidates all downstream KV-cache states for the conversation portion. AdaptiveCache solves precisely this: preserving the conversation history as an exact-token stable prefix instead of replacing it with a new summary string.

- **Custom instructions = task-aware compression:** Compaction's `instructions` parameter allows the developer to provide a task-specific summarization prompt ("Focus on preserving code snippets, variable names, and technical decisions"). This is analogous to AdaptiveCache's task-aware importance signal — both recognize that which content to preserve depends on the current task. AdaptiveCache's attention-based importance signal is the online equivalent of what compaction's custom instructions do statically.

**Key differences from AdaptiveCache:**

1. **Summarization generates new tokens, invalidating prefix KV-cache:** Every compaction event replaces earlier conversation turns with a freshly generated summary. This summary is a new byte sequence — it has never appeared before in any prior request. No downstream prefix KV-cache system can recognize it as a shared prefix with any prior API call. AdaptiveCache's central contribution is ensuring that the compacted context is composed only of tokens that existed in the prior context, reordered for layout stability, so the stable prefix component is byte-for-byte reproducible across consecutive agent steps.

2. **Lossy by design:** Compaction's summary discards exact content in favor of a natural-language paraphrase. For agent tasks requiring precise recall — exact API responses, file contents, code snippets, error messages — the summary may fail to preserve the verbatim content needed. AdaptiveCache's stable prefix retains selected tokens exactly, with zero lossy transformation.

3. **Reactive, not proactive:** Compaction triggers only when the threshold is exceeded — it is a corrective mechanism applied after the context has grown too large. AdaptiveCache operates proactively at each step boundary, continuously maintaining an optimal layout before any threshold is hit. This prevents the performance degradation that occurs in the final turns before a compaction event.

4. **Coarse granularity:** Compaction operates at the conversation-turn level — it summarizes a block of prior turns into one summary. AdaptiveCache operates at the token level — it individually scores each token in the context and decides whether it belongs in the stable prefix, volatile suffix, or eviction pool. Token-level granularity allows AdaptiveCache to preserve exactly the right tokens (high-attention, task-relevant) rather than summarizing indiscriminately.

5. **Additional inference cost:** Each compaction event requires an additional LLM call to generate the summary. For a 180,000-token compaction event with a 3,500-token summary output, this is a significant additional cost (the compaction iteration's 180,000 input tokens are billed separately from the main response). AdaptiveCache's compaction pass uses only attention weights already computed during the LLM's normal forward pass — zero additional LLM calls.

6. **No system prompt protection needed:** AdaptiveCache's stable prefix serves the same function as Anthropic's system-prompt caching recommendation: place stable, high-value content in a position that will be cached across turns. But AdaptiveCache does this for the conversation history too (the stable prefix), not just the system prompt. Anthropic's compaction protects the system prompt by leaving it out of compaction; AdaptiveCache protects all stable content by placing it in a layout-stable prefix position.

**AdaptiveCache synthesis:** Anthropic's server-side compaction is the production baseline against which AdaptiveCache should be measured. It is the state-of-the-art approach for context management as deployed by the model provider itself. The key gap — generating new summary tokens that invalidate the conversation prefix KV-cache — is precisely the gap AdaptiveCache is designed to close. AdaptiveCache's approach of token-level selection and layout-preserving reordering is the alternative that maintains exact-token fidelity and prefix-cache compatibility that summarization-based compaction cannot provide. The fact that Anthropic explicitly recommends combining compaction with prompt caching (keeping the system prompt cached separately) illustrates the tension: prompt caching is desirable, but compaction's generated summaries undermine it for the conversation portion. AdaptiveCache resolves this tension by making the conversation portion prefix-cacheable too.

## Related Pages / Citations to Follow Up

- [prefix-caching.md](prefix-caching.md) — why new summary tokens invalidate the KV-cache; the cost model that compaction's approach cannot exploit
- [context-management.md](context-management.md) — taxonomy: compaction fits as Summarization-Based Compression (Category 4); it is the production API version of the same approach as ReSuM and SUPO
- [resum.md](resum.md) — ReSuM is the closest research analog to compaction: both are plug-and-play, both generate summary text, both are training-optional
- [summarization-rl.md](summarization-rl.md) — SUPO formalizes the same summarization approach via RL; compaction is the production (non-RL) version
- [openhands-condensation.md](openhands-condensation.md) — OpenHands context condensation implements the same strategy at the framework level rather than the API level
- [lost-in-middle.md](lost-in-middle.md) — "context rot" in Anthropic docs maps directly to Liu et al.'s U-shaped position-bias finding; compaction is Anthropic's response to context rot
- [llmlingua.md](llmlingua.md) — LLMLingua is token-level prompt compression; compaction is turn-level summarization; both produce new text that is not prefix-cache-compatible
