# Compaction: A Comprehensive Technical Reference

Compaction is the system that prevents conversations from exceeding the model's context window. It is not a single algorithm — it is a layered pipeline of four distinct strategies that run in a defined order on every query turn, plus reactive fallbacks for emergencies. The core code lives in `src/services/compact/`.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [The Per-Turn Pipeline in `query.ts`](#2-the-per-turn-pipeline-in-queryts)
3. [Strategy 1: Snip](#3-strategy-1-snip)
4. [Strategy 2: Microcompact](#4-strategy-2-microcompact)
5. [Strategy 3: Context Collapse](#5-strategy-3-context-collapse)
6. [Strategy 4: Autocompact](#6-strategy-4-autocompact)
7. [Full Compaction (`compactConversation`)](#7-full-compaction-compactconversation)
8. [The Summarization Prompt](#8-the-summarization-prompt)
9. [The Forked Agent & Prompt Cache Sharing](#9-the-forked-agent--prompt-cache-sharing)
10. [Prompt-Too-Long Retry Loop](#10-prompt-too-long-retry-loop)
11. [Session Memory Compaction](#11-session-memory-compaction)
12. [Partial Compaction](#12-partial-compaction)
13. [The CompactionResult and Post-Compact Assembly](#13-the-compactionresult-and-post-compact-assembly)
14. [Post-Compact Attachments](#14-post-compact-attachments)
15. [The Compact Boundary Message](#15-the-compact-boundary-message)
16. [Autocompact Thresholds and Configuration](#16-autocompact-thresholds-and-configuration)
17. [The Circuit Breaker](#17-the-circuit-breaker)
18. [Post-Compact Cleanup](#18-post-compact-cleanup)
19. [The `/compact` Slash Command](#19-the-compact-slash-command)
20. [Plugin Hooks](#20-plugin-hooks)
21. [UI Components](#21-ui-components)
22. [Telemetry Events](#22-telemetry-events)
23. [Feature Flags and Environment Variables](#23-feature-flags-and-environment-variables)
24. [Data Flow Diagram](#24-data-flow-diagram)

---

## 1. Architecture Overview

Every query in the system flows through `src/query.ts`. Before the model is called, the messages go through up to four context-management stages in this fixed order:

```
incoming messages
       │
       ▼
  [1] SNIP        ← feature-gated; removes old history chunks
       │
       ▼
  [2] MICROCOMPACT ← clears/cache-edits old tool results
       │
       ▼
  [3] CONTEXT      ← feature-gated; context-collapse projection
     COLLAPSE
       │
       ▼
  [4] AUTOCOMPACT  ← full LLM summarization if still over threshold
       │
       ▼
   API call
```

If the API call itself returns a prompt-too-long error, a **reactive compact** fires as an emergency recovery path.

There is also a **manual `/compact` command** that users invoke directly, which bypasses the threshold check and always runs.

---

## 2. The Per-Turn Pipeline in `query.ts`

The pipeline is explicit and sequential in `src/query.ts`:

```typescript
// src/query.ts (lines 396–535)

// [1] SNIP
let snipTokensFreed = 0
if (feature('HISTORY_SNIP')) {
  queryCheckpoint('query_snip_start')
  const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery)
  messagesForQuery = snipResult.messages
  snipTokensFreed = snipResult.tokensFreed
  if (snipResult.boundaryMessage) {
    yield snipResult.boundaryMessage
  }
  queryCheckpoint('query_snip_end')
}

// [2] MICROCOMPACT
queryCheckpoint('query_microcompact_start')
const microcompactResult = await deps.microcompact(
  messagesForQuery,
  toolUseContext,
  querySource,
)
messagesForQuery = microcompactResult.messages
const pendingCacheEdits = feature('CACHED_MICROCOMPACT')
  ? microcompactResult.compactionInfo?.pendingCacheEdits
  : undefined
queryCheckpoint('query_microcompact_end')

// [3] CONTEXT COLLAPSE
if (feature('CONTEXT_COLLAPSE') && contextCollapse) {
  const collapseResult = await contextCollapse.applyCollapsesIfNeeded(
    messagesForQuery,
    toolUseContext,
    querySource,
  )
  messagesForQuery = collapseResult.messages
}

// [4] AUTOCOMPACT
queryCheckpoint('query_autocompact_start')
const { compactionResult, consecutiveFailures } = await deps.autocompact(
  messagesForQuery,
  toolUseContext,
  {
    systemPrompt,
    userContext,
    systemContext,
    toolUseContext,
    forkContextMessages: messagesForQuery,
  },
  querySource,
  tracking,
  snipTokensFreed,        // ← passed so threshold math accounts for snip
)
queryCheckpoint('query_autocompact_end')

if (compactionResult) {
  const postCompactMessages = buildPostCompactMessages(compactionResult)
  for (const message of postCompactMessages) {
    yield message           // ← boundary + summary streamed to REPL
  }
  messagesForQuery = postCompactMessages   // ← continue current query with compact result
}
```

Key design note: `snipTokensFreed` is passed all the way into autocompact. This is because snip removes old messages but the surviving assistant message's `.usage` field still reflects the pre-snip context window — `tokenCountWithEstimation` reads from that usage, so without the manual offset, autocompact's threshold check would be wrong.

---

## 3. Strategy 1: Snip

**File:** `src/services/compact/snipCompact.js` (feature-gated, not in checked-in sources)

Snip is the lightest intervention. It removes old blocks of history in chunks — think of it as a sliding window that drops the oldest material when the buffer gets large. It runs before microcompact so both can fire on the same turn.

The key integration points:
- `snipResult.messages` — the pruned message array
- `snipResult.tokensFreed` — rough token count removed (used by autocompact threshold)
- `snipResult.boundaryMessage` — optional boundary marker yielded to the REPL

---

## 4. Strategy 2: Microcompact

**File:** `src/services/compact/microCompact.ts`

Microcompact does **not** call the model. Instead, it reduces the payload of tool results — the largest per-turn contributor to context size. Only a specific set of tools have their results compacted:

```typescript
// src/services/compact/microCompact.ts (lines 41–50)
const COMPACTABLE_TOOLS = new Set<string>([
  FILE_READ_TOOL_NAME,
  ...SHELL_TOOL_NAMES,
  GREP_TOOL_NAME,
  GLOB_TOOL_NAME,
  WEB_SEARCH_TOOL_NAME,
  WEB_FETCH_TOOL_NAME,
  FILE_EDIT_TOOL_NAME,
  FILE_WRITE_TOOL_NAME,
])
```

There are two sub-strategies inside microcompact, tried in this order:

### 4a. Time-Based Microcompact

If the gap since the last assistant message exceeds a configured threshold (from GrowthBook `tengu_slate_heron`), the server-side prompt cache has certainly expired anyway — so we directly replace old tool result content in the message objects.

```typescript
// src/services/compact/microCompact.ts (lines 422–443)
export function evaluateTimeBasedTrigger(
  messages: Message[],
  querySource: QuerySource | undefined,
): { gapMinutes: number; config: TimeBasedMCConfig } | null {
  const config = getTimeBasedMCConfig()
  if (!config.enabled || !querySource || !isMainThreadSource(querySource)) {
    return null
  }
  const lastAssistant = messages.findLast(m => m.type === 'assistant')
  if (!lastAssistant) {
    return null
  }
  const gapMinutes =
    (Date.now() - new Date(lastAssistant.timestamp).getTime()) / 60_000
  if (!Number.isFinite(gapMinutes) || gapMinutes < config.gapThresholdMinutes) {
    return null
  }
  return { gapMinutes, config }
}
```

When it fires, all compactable tool results except the most recent `keepRecent` (from GrowthBook config) are replaced with the literal string `[Old tool result content cleared]`:

```typescript
// src/services/compact/microCompact.ts (lines 469–493)
const keepSet = new Set(compactableIds.slice(-keepRecent))
const clearSet = new Set(compactableIds.filter(id => !keepSet.has(id)))

const result: Message[] = messages.map(message => {
  if (message.type !== 'user' || !Array.isArray(message.message.content)) {
    return message
  }
  const newContent = message.message.content.map(block => {
    if (
      block.type === 'tool_result' &&
      clearSet.has(block.tool_use_id) &&
      block.content !== TIME_BASED_MC_CLEARED_MESSAGE
    ) {
      tokensSaved += calculateToolResultTokens(block)
      return { ...block, content: TIME_BASED_MC_CLEARED_MESSAGE }
    }
    return block
  })
  // ...
})
```

After time-based microcompact fires, the module-level cached MC state is reset (it would be stale since we just invalidated the server cache), and the function returns — the cached path is skipped.

### 4b. Cached Microcompact (Ant-Only)

When the cache is warm, we can do something more surgical: instead of mutating the local messages, we send `cache_edits` to the API layer. This instructs the server to delete the cached versions of old tool results without invalidating the rest of the cached prefix.

The local message objects are **not modified**. The pending edits are attached to `compactionInfo.pendingCacheEdits` and consumed by the API layer before the next request:

```typescript
// src/services/compact/microCompact.ts (lines 305–398)
async function cachedMicrocompactPath(
  messages: Message[],
  querySource: QuerySource | undefined,
): Promise<MicrocompactResult> {
  // ...register all compactable tool IDs in cachedMCState...
  
  const toolsToDelete = mod.getToolResultsToDelete(state)

  if (toolsToDelete.length > 0) {
    const cacheEdits = mod.createCacheEditsBlock(state, toolsToDelete)
    if (cacheEdits) {
      pendingCacheEdits = cacheEdits     // queued for API layer
    }
    // Return messages UNCHANGED — cache_reference and cache_edits are added at API layer
    return {
      messages,                          // ← local messages untouched
      compactionInfo: {
        pendingCacheEdits: {
          trigger: 'auto',
          deletedToolIds: toolsToDelete,
          baselineCacheDeletedTokens: baseline,
        },
      },
    }
  }
  return { messages }
}
```

The baseline `cache_deleted_input_tokens` from the last API response is captured so that after the response, the per-operation delta can be computed (the API reports this as a cumulative value).

---

## 5. Strategy 3: Context Collapse

**File:** `src/services/contextCollapse/` (ant-only feature, `CONTEXT_COLLAPSE` flag)

Context collapse is a more granular alternative to full compaction. Rather than replacing the entire history with a summary, it selectively "collapses" individual sections of the context — akin to folding away resolved sub-problems — via a commit log that is replayed as a projection on every turn. This runs before autocompact: if collapse gets the token count under the autocompact threshold, autocompact is a no-op.

When context collapse is enabled, autocompact is suppressed (`shouldAutoCompact` returns false for `marble_origami` query sources and when `isContextCollapseEnabled()` is true) to avoid races.

---

## 6. Strategy 4: Autocompact

**File:** `src/services/compact/autoCompact.ts`

This is the last proactive line of defense. `autoCompactIfNeeded` is called after snip, microcompact, and context collapse. It first checks whether compaction is needed at all.

### Threshold Check

```typescript
// src/services/compact/autoCompact.ts (lines 160–238)
export async function shouldAutoCompact(
  messages: Message[],
  model: string,
  querySource?: QuerySource,
  snipTokensFreed = 0,
): Promise<boolean> {
  // Recursion guards
  if (querySource === 'session_memory' || querySource === 'compact') {
    return false
  }
  if (!isAutoCompactEnabled()) {
    return false
  }
  // Reactive-only or context-collapse modes suppress proactive autocompact
  // ...

  const tokenCount = tokenCountWithEstimation(messages) - snipTokensFreed
  const threshold = getAutoCompactThreshold(model)

  const { isAboveAutoCompactThreshold } = calculateTokenWarningState(
    tokenCount,
    model,
  )
  return isAboveAutoCompactThreshold
}
```

### Threshold Calculation

```typescript
// src/services/compact/autoCompact.ts (lines 62–91)
export const AUTOCOMPACT_BUFFER_TOKENS = 13_000

export function getAutoCompactThreshold(model: string): number {
  const effectiveContextWindow = getEffectiveContextWindowSize(model)
  // effectiveContextWindow = contextWindowForModel - min(maxOutputTokens, 20_000)
  const autocompactThreshold =
    effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS
  // ...
  return autocompactThreshold
}
```

So for a model with a 200k context window and 8k max output tokens:
- `effectiveContextWindow` = 200,000 − 8,000 = 192,000
- `autocompactThreshold` = 192,000 − 13,000 = **179,000 tokens**

### Autocompact Execution Order

When the threshold is exceeded, `autoCompactIfNeeded` tries two approaches in order:

```typescript
// src/services/compact/autoCompact.ts (lines 241–351)
export async function autoCompactIfNeeded(...): Promise<...> {
  // 1. Circuit breaker check
  if (tracking?.consecutiveFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES) {
    return { wasCompacted: false }
  }

  const shouldCompact = await shouldAutoCompact(
    messages, model, querySource, snipTokensFreed,
  )
  if (!shouldCompact) {
    return { wasCompacted: false }
  }

  // 2. Try session memory compaction first (cheaper — no LLM call)
  const sessionMemoryResult = await trySessionMemoryCompaction(
    messages,
    toolUseContext.agentId,
    recompactionInfo.autoCompactThreshold,
  )
  if (sessionMemoryResult) {
    setLastSummarizedMessageId(undefined)
    runPostCompactCleanup(querySource)
    notifyCompaction(...)
    markPostCompaction()
    return { wasCompacted: true, compactionResult: sessionMemoryResult }
  }

  // 3. Fall back to full LLM compaction
  try {
    const compactionResult = await compactConversation(
      messages,
      toolUseContext,
      cacheSafeParams,
      true,         // suppressFollowUpQuestions
      undefined,    // no custom instructions
      true,         // isAutoCompact
      recompactionInfo,
    )
    setLastSummarizedMessageId(undefined)
    runPostCompactCleanup(querySource)
    return { wasCompacted: true, compactionResult, consecutiveFailures: 0 }
  } catch (error) {
    const nextFailures = (tracking?.consecutiveFailures ?? 0) + 1
    return { wasCompacted: false, consecutiveFailures: nextFailures }
  }
}
```

---

## 7. Full Compaction (`compactConversation`)

**File:** `src/services/compact/compact.ts`

This is the primary summarization path. It is called by autocompact (with `isAutoCompact: true`) and the `/compact` slash command (with `isAutoCompact: false`).

### Step-by-Step

**Step 1: Measure pre-compact token count**
```typescript
const preCompactTokenCount = tokenCountWithEstimation(messages)
```

**Step 2: Execute PreCompact hooks**
```typescript
context.setSDKStatus?.('compacting')
const hookResult = await executePreCompactHooks(
  {
    trigger: isAutoCompact ? 'auto' : 'manual',
    customInstructions: customInstructions ?? null,
  },
  context.abortController.signal,
)
customInstructions = mergeHookInstructions(
  customInstructions,
  hookResult.newCustomInstructions,
)
```

Hooks can inject additional custom instructions for the summarizer (merged after any user-provided instructions).

**Step 3: Prepare the summary request**
```typescript
const compactPrompt = getCompactPrompt(customInstructions)
const summaryRequest = createUserMessage({ content: compactPrompt })
```

**Step 4: Stream the summary in a retry loop**

The summary is requested via `streamCompactSummary`. If the response text begins with the `PROMPT_TOO_LONG_ERROR_MESSAGE` sentinel, old API-round groups are truncated from the head and the request is retried (up to `MAX_PTL_RETRIES = 3` times):

```typescript
let messagesToSummarize = messages
let ptlAttempts = 0
for (;;) {
  summaryResponse = await streamCompactSummary({
    messages: messagesToSummarize,
    summaryRequest,
    appState,
    context,
    preCompactTokenCount,
    cacheSafeParams: retryCacheSafeParams,
  })
  summary = getAssistantMessageText(summaryResponse)
  if (!summary?.startsWith(PROMPT_TOO_LONG_ERROR_MESSAGE)) break

  ptlAttempts++
  const truncated = ptlAttempts <= MAX_PTL_RETRIES
    ? truncateHeadForPTLRetry(messagesToSummarize, summaryResponse)
    : null
  if (!truncated) throw new Error(ERROR_MESSAGE_PROMPT_TOO_LONG)
  messagesToSummarize = truncated
  retryCacheSafeParams = { ...retryCacheSafeParams, forkContextMessages: truncated }
}
```

**Step 5: Clear file/memory caches**
```typescript
const preCompactReadFileState = cacheToObject(context.readFileState)
context.readFileState.clear()
context.loadedNestedMemoryPaths?.clear()
```

**Step 6: Build post-compact attachments** (run in parallel)
```typescript
const [fileAttachments, asyncAgentAttachments] = await Promise.all([
  createPostCompactFileAttachments(preCompactReadFileState, context, 5),
  createAsyncAgentAttachmentsIfNeeded(context),
])
// ... also add plan, plan mode, skills, deferred tools delta, agent listing, MCP instructions
```

**Step 7: Run session-start hooks**
```typescript
const hookMessages = await processSessionStartHooks('compact', {
  model: context.options.mainLoopModel,
})
```

**Step 8: Create the compact boundary marker**
```typescript
const boundaryMarker = createCompactBoundaryMessage(
  isAutoCompact ? 'auto' : 'manual',
  preCompactTokenCount ?? 0,
  messages.at(-1)?.uuid,   // ← logicalParentUuid for transcript linking
)
// Carry over discovered tool names so schema filtering stays correct
const preCompactDiscovered = extractDiscoveredToolNames(messages)
if (preCompactDiscovered.size > 0) {
  boundaryMarker.compactMetadata.preCompactDiscoveredTools = [
    ...preCompactDiscovered,
  ].sort()
}
```

**Step 9: Create summary user message**
```typescript
const summaryMessages: UserMessage[] = [
  createUserMessage({
    content: getCompactUserSummaryMessage(summary, suppressFollowUpQuestions, transcriptPath),
    isCompactSummary: true,
    isVisibleInTranscriptOnly: true,
  }),
]
```

**Step 10: Telemetry, cache break notification, session metadata**
```typescript
logEvent('tengu_compact', { preCompactTokenCount, ... })
if (feature('PROMPT_CACHE_BREAK_DETECTION')) {
  notifyCompaction(context.options.querySource ?? 'compact', context.agentId)
}
markPostCompaction()
reAppendSessionMetadata()    // keeps custom title within the 16KB tail window
```

**Step 11: Execute PostCompact hooks**
```typescript
const postCompactHookResult = await executePostCompactHooks(
  { trigger: isAutoCompact ? 'auto' : 'manual', compactSummary: summary },
  context.abortController.signal,
)
```

**Step 12: Return the CompactionResult**
```typescript
return {
  boundaryMarker,
  summaryMessages,
  attachments: postCompactFileAttachments,
  hookResults: hookMessages,
  preCompactTokenCount,
  postCompactTokenCount: compactionCallTotalTokens,
  truePostCompactTokenCount,
  compactionUsage,
}
```

---

## 8. The Summarization Prompt

**File:** `src/services/compact/prompt.ts`

The compact prompt has a carefully layered structure:

```typescript
// src/services/compact/prompt.ts (lines 19–26)
const NO_TOOLS_PREAMBLE = `CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

`
```

This aggressive preamble exists because on Sonnet 4.6+ with adaptive thinking, the model sometimes attempts tool calls during compaction. With `maxTurns: 1` in the forked agent, a denied tool call means no text output and a fallback to the streaming path — costing ~2.79% of compact calls on 4.6 vs 0.01% on 4.5.

The main body instructs the model to produce two XML sections:

```
<analysis>
  [Private scratchpad — chronological walk of every message, identifying
   requests, decisions, file names, code snippets, errors, user feedback]
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
  9. Optional Next Step (with verbatim quote from most recent work)
</summary>
```

The `<analysis>` block is stripped before the summary enters the context:

```typescript
// src/services/compact/prompt.ts (lines 311–334)
export function formatCompactSummary(summary: string): string {
  let formattedSummary = summary

  // Strip analysis section — it's a drafting scratchpad that improves summary
  // quality but has no informational value once the summary is written.
  formattedSummary = formattedSummary.replace(
    /<analysis>[\s\S]*?<\/analysis>/,
    '',
  )

  // Extract and format summary section
  const summaryMatch = formattedSummary.match(/<summary>([\s\S]*?)<\/summary>/)
  if (summaryMatch) {
    const content = summaryMatch[1] || ''
    formattedSummary = formattedSummary.replace(
      /<summary>[\s\S]*?<\/summary>/,
      `Summary:\n${content.trim()}`,
    )
  }

  formattedSummary = formattedSummary.replace(/\n\n+/g, '\n\n')
  return formattedSummary.trim()
}
```

The final user-facing summary message also includes:
- A preamble: "This session is being continued from a previous conversation that ran out of context."
- A link to the full transcript JSONL file for looking up exact details.
- For autocompact: an instruction to "Continue the conversation from where it left off without asking the user any further questions."

When images are present in user messages, they are stripped and replaced with `[image]` or `[document]` placeholders before the compact call. This prevents the compact request itself from hitting prompt-too-long due to image tokens:

```typescript
// src/services/compact/compact.ts (lines 145–200)
export function stripImagesFromMessages(messages: Message[]): Message[] {
  return messages.map(message => {
    if (message.type !== 'user') return message
    // ... replace image/document blocks with text placeholders
    // ... also strip images nested inside tool_result content arrays
  })
}
```

---

## 9. The Forked Agent & Prompt Cache Sharing

**File:** `src/services/compact/compact.ts` → `streamCompactSummary`

By default (`tengu_compact_cache_prefix: true`), the compact summary is generated via a **forked agent** that reuses the main conversation's prompt cache prefix. This is the key cost-optimization: rather than re-sending the entire system prompt, tools, and context messages (which triggers cache creation), the fork inherits the parent's cached prefix.

```typescript
// src/services/compact/compact.ts (lines 1178–1230)
if (promptCacheSharingEnabled) {
  try {
    // DO NOT set maxOutputTokens here — it would change the thinking config,
    // invalidating the cache-key match with the main thread.
    const result = await runForkedAgent({
      promptMessages: [summaryRequest],  // only the summarization request
      cacheSafeParams,                   // ← shared with main thread
      canUseTool: createCompactCanUseTool(),  // all tools denied
      querySource: 'compact',
      forkLabel: 'compact',
      maxTurns: 1,
      skipCacheWrite: true,
      overrides: { abortController: context.abortController },
    })
    const assistantMsg = getLastAssistantMessage(result.messages)
    // Guard: if the fork returned an API error message (e.g., aborted), don't
    // treat it as a valid summary — fall through to streaming path.
    if (assistantMsg && assistantText && !assistantMsg.isApiErrorMessage) {
      return assistantMsg
    }
  } catch (error) {
    // Fall through to streaming fallback
  }
}
```

On failure, it falls back to a direct `queryModelWithStreaming` call. On this path, `maxOutputTokensOverride` can safely be set since there's no cache to share:

```typescript
// src/services/compact/compact.ts (lines 1292–1326)
const streamingGen = queryModelWithStreaming({
  messages: normalizeMessagesForAPI(
    stripImagesFromMessages(
      stripReinjectedAttachments([
        ...getMessagesAfterCompactBoundary(messages),
        summaryRequest,
      ]),
    ),
    context.options.tools,
  ),
  systemPrompt: asSystemPrompt(['You are a helpful AI assistant tasked with summarizing conversations.']),
  thinkingConfig: { type: 'disabled' as const },
  tools,
  signal: context.abortController.signal,
  options: {
    model: context.options.mainLoopModel,
    maxOutputTokensOverride: Math.min(
      COMPACT_MAX_OUTPUT_TOKENS,
      getMaxOutputTokensForModel(context.options.mainLoopModel),
    ),
    querySource: 'compact',
    // ...
  },
})
```

Keep-alive signals are sent during the compact call to prevent remote session WebSocket idle timeouts — compact API calls can take 5–10+ seconds:

```typescript
const activityInterval = isSessionActivityTrackingActive()
  ? setInterval(
      (statusSetter) => {
        sendSessionActivitySignal()
        statusSetter?.('compacting')
      },
      30_000,
      context.setSDKStatus,
    )
  : undefined
```

---

## 10. Prompt-Too-Long Retry Loop

**File:** `src/services/compact/compact.ts` → `truncateHeadForPTLRetry`

When the compact request itself hits a prompt-too-long error (CC-1180), the oldest API-round groups are dropped from the messages being summarized and the request is retried.

API rounds are grouped by `groupMessagesByApiRound`:

```typescript
// src/services/compact/grouping.ts (lines 22–63)
export function groupMessagesByApiRound(messages: Message[]): Message[][] {
  const groups: Message[][] = []
  let current: Message[] = []
  let lastAssistantId: string | undefined

  for (const msg of messages) {
    if (
      msg.type === 'assistant' &&
      msg.message.id !== lastAssistantId &&
      current.length > 0
    ) {
      groups.push(current)
      current = [msg]
    } else {
      current.push(msg)
    }
    if (msg.type === 'assistant') {
      lastAssistantId = msg.message.id
    }
  }
  if (current.length > 0) groups.push(current)
  return groups
}
```

A new assistant message ID signals an API-round boundary. `truncateHeadForPTLRetry` then drops enough groups from the head to cover the token gap:

```typescript
// src/services/compact/compact.ts (lines 243–291)
export function truncateHeadForPTLRetry(
  messages: Message[],
  ptlResponse: AssistantMessage,
): Message[] | null {
  // Strip our own synthetic marker from a previous retry
  const input = messages[0]?.type === 'user' && messages[0].isMeta &&
    messages[0].message.content === PTL_RETRY_MARKER
    ? messages.slice(1)
    : messages

  const groups = groupMessagesByApiRound(input)
  if (groups.length < 2) return null

  const tokenGap = getPromptTooLongTokenGap(ptlResponse)
  let dropCount: number
  if (tokenGap !== undefined) {
    let acc = 0
    dropCount = 0
    for (const g of groups) {
      acc += roughTokenCountEstimationForMessages(g)
      dropCount++
      if (acc >= tokenGap) break
    }
  } else {
    // Some Vertex/Bedrock error formats don't include a token gap — drop 20%
    dropCount = Math.max(1, Math.floor(groups.length * 0.2))
  }

  dropCount = Math.min(dropCount, groups.length - 1)
  if (dropCount < 1) return null

  const sliced = groups.slice(dropCount).flat()
  // If truncation left an assistant-first sequence, prepend a synthetic
  // user message so the API's "first message must be user" rule holds.
  if (sliced[0]?.type === 'assistant') {
    return [
      createUserMessage({ content: PTL_RETRY_MARKER, isMeta: true }),
      ...sliced,
    ]
  }
  return sliced
}
```

---

## 11. Session Memory Compaction

**File:** `src/services/compact/sessionMemoryCompact.ts`

Session memory compaction is an experiment (gated behind `tengu_session_memory && tengu_sm_compact`) that avoids a fresh LLM summarization call by reusing an already-extracted session memory file. It is tried first by `autoCompactIfNeeded`.

### Prerequisites

- Session memory feature must be enabled
- A non-empty session memory file must exist
- The memory content must not be just the template (no actual extracted content)

### How the "Keep" Slice Is Calculated

The algorithm works backwards from a known `lastSummarizedMessageId`:

```typescript
// src/services/compact/sessionMemoryCompact.ts (lines 324–397)
export function calculateMessagesToKeepIndex(
  messages: Message[],
  lastSummarizedIndex: number,
): number {
  const config = getSessionMemoryCompactConfig()
  // Start from the message AFTER the last summarized one
  let startIndex = lastSummarizedIndex >= 0
    ? lastSummarizedIndex + 1
    : messages.length

  let totalTokens = 0
  let textBlockMessageCount = 0
  // Measure what we already have from startIndex forward
  for (let i = startIndex; i < messages.length; i++) {
    totalTokens += estimateMessageTokens([messages[i]!])
    if (hasTextBlocks(messages[i]!)) textBlockMessageCount++
  }

  // Check caps/minimums
  if (totalTokens >= config.maxTokens) {
    return adjustIndexToPreserveAPIInvariants(messages, startIndex)
  }
  if (totalTokens >= config.minTokens && textBlockMessageCount >= config.minTextBlockMessages) {
    return adjustIndexToPreserveAPIInvariants(messages, startIndex)
  }

  // Expand backwards until both minimums are met or maxTokens cap is hit
  const floor = idx === -1 ? 0 : idx + 1   // don't cross a prior compact boundary
  for (let i = startIndex - 1; i >= floor; i--) {
    totalTokens += estimateMessageTokens([messages[i]!])
    if (hasTextBlocks(messages[i]!)) textBlockMessageCount++
    startIndex = i
    if (totalTokens >= config.maxTokens) break
    if (totalTokens >= config.minTokens && textBlockMessageCount >= config.minTextBlockMessages) break
  }

  return adjustIndexToPreserveAPIInvariants(messages, startIndex)
}
```

**Default configuration:**
```typescript
// src/services/compact/sessionMemoryCompact.ts (lines 57–61)
export const DEFAULT_SM_COMPACT_CONFIG: SessionMemoryCompactConfig = {
  minTokens: 10_000,
  minTextBlockMessages: 5,
  maxTokens: 40_000,
}
```

These can be overridden via GrowthBook `tengu_sm_compact_config`.

### API Invariants Preservation

A critical edge case: you cannot split `tool_use`/`tool_result` pairs across the summarized/kept boundary, and you cannot include only part of a streaming batch (multiple assistant messages sharing the same `message.id`). `adjustIndexToPreserveAPIInvariants` handles both:

```typescript
// src/services/compact/sessionMemoryCompact.ts (lines 232–313)
export function adjustIndexToPreserveAPIInvariants(
  messages: Message[],
  startIndex: number,
): number {
  // Step 1: find any tool_result IDs in the kept range that need their
  // corresponding tool_use in messages before startIndex
  // → expand startIndex backwards to include the tool_use

  // Step 2: find any assistant messages in the kept range that share a
  // message.id with assistant messages before startIndex (thinking blocks)
  // → expand startIndex backwards to include the thinking block message
  
  return adjustedIndex
}
```

### Threshold Check

If even session memory compaction would leave a context over the autocompact threshold (because too many messages need to be kept), it returns null and falls back to full LLM compaction:

```typescript
if (
  autoCompactThreshold !== undefined &&
  postCompactTokenCount >= autoCompactThreshold
) {
  logEvent('tengu_sm_compact_threshold_exceeded', { ... })
  return null
}
```

---

## 12. Partial Compaction

**File:** `src/services/compact/compact.ts` → `partialCompactConversation`

Partial compaction lets users summarize only a portion of the conversation — either everything after a selected message (`direction: 'from'`) or everything before it (`direction: 'up_to'`). This is triggered from the message selector UI.

The direction affects which messages are sent to the model and which are preserved verbatim:

```typescript
// src/services/compact/compact.ts (lines 780–800)
const messagesToSummarize =
  direction === 'up_to'
    ? allMessages.slice(0, pivotIndex)
    : allMessages.slice(pivotIndex)

const messagesToKeep =
  direction === 'up_to'
    ? allMessages
        .slice(pivotIndex)
        .filter(
          m =>
            m.type !== 'progress' &&
            !isCompactBoundaryMessage(m) &&
            !(m.type === 'user' && m.isCompactSummary),
        )
    : allMessages.slice(0, pivotIndex).filter(m => m.type !== 'progress')
```

For `up_to`: old compact boundaries and summaries are stripped from `messagesToKeep` because the new summary will precede them, and a stale boundary in the kept tail would confuse the backward scan in `findLastCompactBoundaryIndex`.

The prompt used for partial compaction varies by direction:
- `'from'`: `PARTIAL_COMPACT_PROMPT` — "summarize the recent messages that follow earlier retained context"
- `'up_to'`: `PARTIAL_COMPACT_UP_TO_PROMPT` — "summarize this conversation; newer messages will follow after your summary"

For re-injection of deferred tools and MCP instructions, the partial path passes `messagesToKeep` to the delta functions so it only re-announces things that were lost in the summarized portion (not things the model can still see in the preserved tail).

The boundary is annotated with `preservedSegment` metadata for the transcript loader:

```typescript
// src/services/compact/compact.ts (lines 349–367)
export function annotateBoundaryWithPreservedSegment(
  boundary: SystemCompactBoundaryMessage,
  anchorUuid: UUID,
  messagesToKeep: readonly Message[] | undefined,
): SystemCompactBoundaryMessage {
  const keep = messagesToKeep ?? []
  if (keep.length === 0) return boundary
  return {
    ...boundary,
    compactMetadata: {
      ...boundary.compactMetadata,
      preservedSegment: {
        headUuid: keep[0]!.uuid,
        anchorUuid,
        tailUuid: keep.at(-1)!.uuid,
      },
    },
  }
}
```

- For `'from'` (prefix-preserving): `anchorUuid = boundaryMarker.uuid`
- For `'up_to'` (suffix-preserving): `anchorUuid = summaryMessages.at(-1).uuid`

---

## 13. The CompactionResult and Post-Compact Assembly

**File:** `src/services/compact/compact.ts`

Every compaction path returns a `CompactionResult`:

```typescript
// src/services/compact/compact.ts (lines 299–310)
export interface CompactionResult {
  boundaryMarker: SystemMessage
  summaryMessages: UserMessage[]
  attachments: AttachmentMessage[]
  hookResults: HookResultMessage[]
  messagesToKeep?: Message[]      // for partial and session-memory compact
  userDisplayMessage?: string
  preCompactTokenCount?: number
  postCompactTokenCount?: number
  truePostCompactTokenCount?: number
  compactionUsage?: ReturnType<typeof getTokenUsage>
}
```

`buildPostCompactMessages` assembles the final array in a fixed order:

```typescript
// src/services/compact/compact.ts (lines 330–338)
export function buildPostCompactMessages(result: CompactionResult): Message[] {
  return [
    result.boundaryMarker,
    ...result.summaryMessages,
    ...(result.messagesToKeep ?? []),
    ...result.attachments,
    ...result.hookResults,
  ]
}
```

This ordering matters:
1. **Boundary** — tells `getMessagesAfterCompactBoundary` where to slice
2. **Summary** — the LLM-generated context replacement
3. **messagesToKeep** — raw recent messages (partial/session-memory compact only)
4. **Attachments** — re-injected files, skills, plan, tools delta, etc.
5. **Hook results** — outputs from session-start hooks (e.g., CLAUDE.md)

---

## 14. Post-Compact Attachments

**File:** `src/services/compact/compact.ts`

After compaction, the model loses all context that was in the summarized messages. A set of attachments is re-injected to restore the most important working state.

### File Attachments (`createPostCompactFileAttachments`)

Recently accessed files are restored from the `readFileState` cache (which tracks every file the `FileReadTool` has read). Constraints:

- Max 5 files (`POST_COMPACT_MAX_FILES_TO_RESTORE`)
- Max 5,000 tokens per file (`POST_COMPACT_MAX_TOKENS_PER_FILE`)
- Total budget of 50,000 tokens (`POST_COMPACT_TOKEN_BUDGET`)
- Files already present in `messagesToKeep` are skipped (would be pure waste)
- Plan files and CLAUDE.md files are excluded (handled via separate attachments or hooks)

```typescript
// src/services/compact/compact.ts (lines 1415–1463)
export async function createPostCompactFileAttachments(
  readFileState: Record<string, { content: string; timestamp: number }>,
  toolUseContext: ToolUseContext,
  maxFiles: number,
  preservedMessages: Message[] = [],
): Promise<AttachmentMessage[]> {
  const preservedReadPaths = collectReadToolFilePaths(preservedMessages)
  const recentFiles = Object.entries(readFileState)
    .map(([filename, state]) => ({ filename, ...state }))
    .filter(
      file =>
        !shouldExcludeFromPostCompactRestore(file.filename, toolUseContext.agentId) &&
        !preservedReadPaths.has(expandPath(file.filename)),
    )
    .sort((a, b) => b.timestamp - a.timestamp)   // most recent first
    .slice(0, maxFiles)
  // ... re-read via FileReadTool, apply token budget
}
```

### Skill Attachments (`createSkillAttachmentIfNeeded`)

Any skills that were invoked during the session are re-injected. Per-skill cap is 5,000 tokens; total budget is 25,000 tokens. Skills are sorted most-recent-first, and each is truncated at the head (where setup instructions typically live) rather than dropped entirely:

```typescript
// src/services/compact/compact.ts (lines 1494–1534)
export function createSkillAttachmentIfNeeded(agentId?: string): AttachmentMessage | null {
  const invokedSkills = getInvokedSkillsForAgent(agentId)
  if (invokedSkills.size === 0) return null

  let usedTokens = 0
  const skills = Array.from(invokedSkills.values())
    .sort((a, b) => b.invokedAt - a.invokedAt)     // most-recent-first
    .map(skill => ({
      name: skill.skillName,
      path: skill.skillPath,
      content: truncateToTokens(skill.content, POST_COMPACT_MAX_TOKENS_PER_SKILL),
    }))
    .filter(skill => {
      const tokens = roughTokenCountEstimation(skill.content)
      if (usedTokens + tokens > POST_COMPACT_SKILLS_TOKEN_BUDGET) return false
      usedTokens += tokens
      return true
    })
  // ...
}
```

### Other Attachments

| Attachment | Purpose |
|---|---|
| **Plan file** | If a plan file exists for the session, re-inject its content |
| **Plan mode** | If in plan mode, re-inject the plan mode reminder so the model doesn't forget |
| **Deferred tools delta** | Re-announce tools that were loaded via `ToolSearchTool` (their schemas were compacted away) |
| **Agent listing delta** | Re-announce async agent statuses |
| **MCP instructions delta** | Re-announce MCP server instructions |
| **Async agent statuses** | Any background agents still running or with unread results |

---

## 15. The Compact Boundary Message

**File:** `src/utils/messages.ts`

The compact boundary is a special system message of `subtype: 'compact_boundary'` inserted as the first message of every compacted context:

```typescript
// src/utils/messages.ts (lines 4530–4555)
export function createCompactBoundaryMessage(
  trigger: 'manual' | 'auto',
  preTokens: number,
  lastPreCompactMessageUuid?: UUID,
  userContext?: string,
  messagesSummarized?: number,
): SystemCompactBoundaryMessage {
  return {
    type: 'system',
    subtype: 'compact_boundary',
    content: `Conversation compacted`,
    isMeta: false,
    timestamp: new Date().toISOString(),
    uuid: randomUUID(),
    level: 'info',
    compactMetadata: {
      trigger,            // 'manual' or 'auto'
      preTokens,          // token count before compaction
      userContext,        // user-provided instructions (partial compact)
      messagesSummarized, // count of messages summarized (partial compact)
    },
    ...(lastPreCompactMessageUuid && {
      logicalParentUuid: lastPreCompactMessageUuid,  // for transcript chain linking
    }),
  }
}
```

The boundary also carries:
- `preCompactDiscoveredTools` — tool names that were loaded via `ToolSearchTool` and need to stay in the schema filter after the summary removes their `tool_reference` blocks
- `preservedSegment` — for partial/session-memory compact: `{ headUuid, anchorUuid, tailUuid }` for the transcript loader to stitch the chain correctly

`getMessagesAfterCompactBoundary` uses this boundary to efficiently slice the message array on every API call:

```typescript
// src/utils/messages.ts (lines 4643–4656)
export function getMessagesAfterCompactBoundary<T extends Message | NormalizedMessage>(
  messages: T[],
  options?: { includeSnipped?: boolean },
): T[] {
  const boundaryIndex = findLastCompactBoundaryIndex(messages)
  const sliced = boundaryIndex === -1 ? messages : messages.slice(boundaryIndex)
  if (!options?.includeSnipped && feature('HISTORY_SNIP')) {
    // also project snipped-message view
    return projectSnippedView(sliced as Message[]) as T[]
  }
  return sliced
}
```

---

## 16. Autocompact Thresholds and Configuration

**File:** `src/services/compact/autoCompact.ts`

### Token Warning States

```typescript
// src/services/compact/autoCompact.ts (lines 62–65)
export const AUTOCOMPACT_BUFFER_TOKENS = 13_000
export const WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
export const ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
export const MANUAL_COMPACT_BUFFER_TOKENS = 3_000
```

For any given model, with autocompact enabled:

| State | Condition |
|---|---|
| Normal | `tokens < threshold - 20k` |
| Warning (yellow) | `tokens >= threshold - 20k` |
| Error (red) | `tokens >= threshold - 20k` (same band currently) |
| **Autocompact fires** | `tokens >= threshold` |
| Blocking limit (autocompact off) | `tokens >= effectiveWindow - 3k` |

### `calculateTokenWarningState`

```typescript
// src/services/compact/autoCompact.ts (lines 93–145)
export function calculateTokenWarningState(
  tokenUsage: number,
  model: string,
): {
  percentLeft: number
  isAboveWarningThreshold: boolean
  isAboveErrorThreshold: boolean
  isAboveAutoCompactThreshold: boolean
  isAtBlockingLimit: boolean
} {
  const autoCompactThreshold = getAutoCompactThreshold(model)
  const threshold = isAutoCompactEnabled()
    ? autoCompactThreshold
    : getEffectiveContextWindowSize(model)

  const percentLeft = Math.max(
    0,
    Math.round(((threshold - tokenUsage) / threshold) * 100),
  )
  // ...
}
```

This function drives the context window usage bar in the UI.

### Environment Variables

| Variable | Effect |
|---|---|
| `DISABLE_COMPACT` | Disables all compaction (autocompact and manual) |
| `DISABLE_AUTO_COMPACT` | Disables autocompact only; manual `/compact` still works |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | Caps the effective context window size used for threshold calculation |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | Sets threshold as a percentage of effective window (for testing) |
| `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` | Overrides the blocking limit when autocompact is off |
| `ENABLE_CLAUDE_CODE_SM_COMPACT` | Force-enables session memory compaction experiment |
| `DISABLE_CLAUDE_CODE_SM_COMPACT` | Force-disables session memory compaction experiment |

### User Settings

`autoCompactEnabled` in the global user config (settable via the Settings UI) controls whether autocompact is active. It defaults to true.

---

## 17. The Circuit Breaker

**File:** `src/services/compact/autoCompact.ts`

To prevent sessions where the context is irrecoverably over the limit from hammering the API with doomed compact attempts on every turn, a circuit breaker tracks consecutive failures:

```typescript
// src/services/compact/autoCompact.ts (lines 70, 258–265)
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3

if (
  tracking?.consecutiveFailures !== undefined &&
  tracking.consecutiveFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
) {
  return { wasCompacted: false }
}
```

Background data showed 1,279 sessions with 50+ consecutive failures (up to 3,272) wasting ~250k API calls/day globally before this was added.

The failure count is tracked in the `AutoCompactTrackingState` threaded through the query loop:

```typescript
export type AutoCompactTrackingState = {
  compacted: boolean
  turnCounter: number
  turnId: string
  consecutiveFailures?: number
}
```

On success, `consecutiveFailures` resets to 0. On failure, it increments. Once it reaches 3, no more autocompact attempts are made for the session.

---

## 18. Post-Compact Cleanup

**File:** `src/services/compact/postCompactCleanup.ts`

After every successful compaction (both auto and manual), caches and module-level state are reset:

```typescript
// src/services/compact/postCompactCleanup.ts (lines 31–77)
export function runPostCompactCleanup(querySource?: QuerySource): void {
  const isMainThreadCompact =
    querySource === undefined ||
    querySource.startsWith('repl_main_thread') ||
    querySource === 'sdk'

  resetMicrocompactState()    // clears cachedMCState and pendingCacheEdits

  if (feature('CONTEXT_COLLAPSE') && isMainThreadCompact) {
    resetContextCollapse()    // clears the commit log
  }

  if (isMainThreadCompact) {
    getUserContext.cache.clear?.()     // forces CLAUDE.md reload on next turn
    resetGetMemoryFilesCache('compact')
  }

  clearSystemPromptSections()
  clearClassifierApprovals()
  clearSpeculativeChecks()
  clearBetaTracingState()
  clearSessionMessagesCache()

  // NOT reset: sentSkillNames — re-injecting the full skill_listing post-compact
  // is pure cache_creation with marginal benefit (~4K tokens/compact)
  // NOT reset: invoked skill content — needed for createSkillAttachmentIfNeeded
}
```

The check for `isMainThreadCompact` is critical: subagents run in the same process and share module-level state with the main thread. Resetting context-collapse state or the memory file cache when a subagent compacts would corrupt the main thread.

---

## 19. The `/compact` Slash Command

**File:** `src/commands/compact/compact.ts`

The manual `/compact` command goes through the same infrastructure but with explicit user invocation:

1. **Try session memory compaction** first (same as autocompact)
2. If unavailable, run microcompact on the messages (reduces payload before summarizing)
3. Call `compactConversation` with `isAutoCompact: false`
4. Apply the result via `buildPostCompactMessages`
5. Update the REPL's message array

The user can pass custom instructions: `/compact Focus on the TypeScript changes and remember which tests failed.` These are merged with any hook-provided instructions and passed into the summarization prompt.

---

## 20. Plugin Hooks

**File:** `src/utils/hooks.ts`

Compaction exposes two plugin hook points:

### PreCompact Hook
Fired before the summarization call. Hooks can:
- Return `newCustomInstructions` (string) to inject additional summarization instructions
- Return `userDisplayMessage` to show a message in the UI

```typescript
await executePreCompactHooks(
  { trigger: 'auto' | 'manual', customInstructions: string | null },
  abortSignal,
)
```

### PostCompact Hook
Fired after the summary has been generated and before the boundary/attachments are assembled. Receives the completed summary text.

```typescript
await executePostCompactHooks(
  { trigger: 'auto' | 'manual', compactSummary: string },
  abortSignal,
)
```

The hook results (`hookResults: HookResultMessage[]`) from session-start hooks are assembled separately and appended after the attachments in `buildPostCompactMessages`.

---

## 21. UI Components

| Component | File | Role |
|---|---|---|
| `CompactBoundaryMessage` | `src/components/messages/CompactBoundaryMessage.tsx` | Renders the "Conversation compacted" divider line in the chat |
| `CompactSummary` | `src/components/CompactSummary.tsx` | Renders the summary content (formatted, not raw XML) |
| `compactWarningHook` | `src/services/compact/compactWarningHook.ts` | React hook that suppresses the "approaching autocompact" warning after a compact already happened |
| `compactWarningState` | `src/services/compact/compactWarningState.ts` | Module-level state for that suppression |
| `usePostCompactSurvey` | `src/components/FeedbackSurvey/usePostCompactSurvey.tsx` | Optionally prompts the user for feedback after compaction |

The context window usage bar in the UI reads from `calculateTokenWarningState` to determine which band (normal/warning/error/blocking) to display.

---

## 22. Telemetry Events

| Event | When fired |
|---|---|
| `tengu_compact` | Full compaction succeeded |
| `tengu_compact_failed` | Full compaction failed (with `reason` field) |
| `tengu_compact_ptl_retry` | Compact request hit prompt-too-long, retrying |
| `tengu_compact_cache_sharing_success` | Forked-agent cache-sharing path succeeded |
| `tengu_compact_cache_sharing_fallback` | Cache-sharing failed, using streaming fallback |
| `tengu_compact_streaming_retry` | Streaming fallback being retried |
| `tengu_auto_compact_succeeded` | Autocompact completed (logged in `query.ts`) |
| `tengu_post_autocompact_turn` | Turn following autocompact |
| `tengu_partial_compact` | Partial compaction succeeded |
| `tengu_partial_compact_failed` | Partial compaction failed |
| `tengu_time_based_microcompact` | Time-based microcompact fired |
| `tengu_cached_microcompact` | Cached microcompact fired |
| `tengu_sm_compact_*` | Session memory compaction events |

The `tengu_compact` event includes a `willRetriggerNextTurn` field that is true when `truePostCompactTokenCount >= autoCompactThreshold` — a strong signal that the resulting context is still too large and the next turn will compact again.

---

## 23. Feature Flags and Environment Variables

| Flag / Env Var | Default | Purpose |
|---|---|---|
| `tengu_compact_cache_prefix` (GB) | `true` | Enable forked-agent prompt cache sharing for compact |
| `tengu_compact_streaming_retry` (GB) | `false` | Retry the streaming fallback on failure |
| `tengu_slate_heron` (GB) | — | Time-based microcompact config (threshold, keepRecent) |
| `tengu_session_memory` (GB) | `false` | Enable session memory feature |
| `tengu_sm_compact` (GB) | `false` | Enable session memory compaction |
| `tengu_sm_compact_config` (GB) | — | Override SM compact thresholds (minTokens, maxTokens, etc.) |
| `tengu_cobalt_raccoon` (GB) | `false` | Reactive-only mode (suppress proactive autocompact) |
| `HISTORY_SNIP` (feature flag) | — | Enable snip strategy |
| `CACHED_MICROCOMPACT` (feature flag) | — | Enable cached microcompact (ant-only) |
| `CONTEXT_COLLAPSE` (feature flag) | — | Enable context collapse (ant-only) |
| `REACTIVE_COMPACT` (feature flag) | — | Enable reactive compact recovery |
| `PROMPT_CACHE_BREAK_DETECTION` (feature flag) | — | Track prompt cache break events |
| `KAIROS` (feature flag) | — | Write session transcript segments for compacted messages |

---

## 24. Data Flow Diagram

```
User sends message
       │
       ▼
  query() called
       │
       ├─► getMessagesAfterCompactBoundary()
       │         ↳ slices from last compact_boundary marker
       │
       ├─► applyToolResultBudget()
       │         ↳ may shrink oversized tool results
       │
       ├─► [HISTORY_SNIP] snipCompactIfNeeded()
       │         ↳ removes old history chunks
       │         ↳ returns tokensFreed for threshold math
       │
       ├─► microcompactMessages()
       │     │
       │     ├─► evaluateTimeBasedTrigger()
       │     │     ↳ if gap > threshold:
       │     │         content-clear old tool results in messages
       │     │         reset cachedMCState
       │     │         ↳ return modified messages
       │     │
       │     └─► [CACHED_MICROCOMPACT] cachedMicrocompactPath()
       │               ↳ register new tool IDs in cachedMCState
       │               ↳ compute toolsToDelete
       │               ↳ queue pendingCacheEdits (consumed at API layer)
       │               ↳ return ORIGINAL messages (not modified)
       │
       ├─► [CONTEXT_COLLAPSE] applyCollapsesIfNeeded()
       │         ↳ project collapsed view of context
       │
       ├─► autoCompactIfNeeded()
       │     │
       │     ├─► shouldAutoCompact()
       │     │     ↳ tokenCount - snipTokensFreed >= threshold?
       │     │     ↳ guards: querySource not 'compact'/'session_memory',
       │     │               autoCompactEnabled, circuit breaker
       │     │
       │     ├─► [if threshold exceeded]
       │     │     trySessionMemoryCompaction()
       │     │         ↳ read lastSummarizedMessageId
       │     │         ↳ read session memory file
       │     │         ↳ calculateMessagesToKeepIndex()
       │     │         ↳ adjustIndexToPreserveAPIInvariants()
       │     │         ↳ build CompactionResult from session memory text
       │     │         ↳ if result still over threshold → return null
       │     │
       │     └─► [if SM compact unavailable]
       │           compactConversation()
       │               ↳ executePreCompactHooks()
       │               ↳ streamCompactSummary()
       │                   ├─► runForkedAgent() (cache-sharing)
       │                   └─► queryModelWithStreaming() (fallback)
       │               ↳ [if PTL error] truncateHeadForPTLRetry() + retry
       │               ↳ clear readFileState cache
       │               ↳ createPostCompactFileAttachments()
       │               ↳ createSkillAttachmentIfNeeded()
       │               ↳ getDeferredToolsDeltaAttachment()
       │               ↳ processSessionStartHooks('compact')
       │               ↳ createCompactBoundaryMessage()
       │               ↳ logEvent('tengu_compact')
       │               ↳ notifyCompaction() + markPostCompaction()
       │               ↳ reAppendSessionMetadata()
       │               ↳ executePostCompactHooks()
       │               ↳ return CompactionResult
       │
       ├─► [if compactionResult]
       │     buildPostCompactMessages()
       │         [boundaryMarker, summaryMessages, messagesToKeep,
       │          attachments, hookResults]
       │     yield each message to REPL
       │     messagesForQuery = postCompactMessages
       │     runPostCompactCleanup()
       │
       └─► API call with final messagesForQuery
```
