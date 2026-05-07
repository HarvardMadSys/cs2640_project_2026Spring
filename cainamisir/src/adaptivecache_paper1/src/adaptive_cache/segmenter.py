"""Block segmentation: parse OpenAI-format messages into typed blocks."""

from __future__ import annotations

import re

import tiktoken

from adaptive_cache.types import Block, BlockType


# Lazy-loaded tokenizer
_enc: tiktoken.Encoding | None = None


def _tokenizer() -> tiktoken.Encoding:
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


def count_tokens(text: str) -> int:
    return len(_tokenizer().encode(text))


# --- Block type detection ---

_ERROR_PATTERNS = re.compile(
    r"(error|exception|traceback|failed|fatal|panic|errno|segfault)",
    re.IGNORECASE,
)

_FILE_PATTERNS = re.compile(
    r"(^(---|===|def |class |import |from |#include|package |func )"
    r"|\.py|\.js|\.ts|\.go|\.rs|\.java|\.c|\.h)",
    re.MULTILINE,
)

_GREP_PATTERNS = re.compile(
    r"(^\d+[:\-]|^[./\w]+:\d+:)",
    re.MULTILINE,
)


def _classify_observation(content: str, tool_name: str | None = None) -> BlockType:
    """Classify a tool result into an observation block type.

    Error detection takes priority over tool name, since error output
    from any tool (bash, file_read, etc.) should be classified as OBS_ERROR.
    """
    if tool_name:
        name = tool_name.lower()
        # For non-shell tools, trust the tool name over content heuristics
        if "grep" in name or "search" in name or "find" in name:
            return BlockType.OBS_GREP
        if "read" in name or "cat" in name or "open" in name or "file" in name:
            return BlockType.OBS_FILE
        # For shell tools, check error patterns first since errors are important
        if "bash" in name or "shell" in name or "exec" in name or "run" in name:
            if _ERROR_PATTERNS.search(content[:500]):
                return BlockType.OBS_ERROR
            return BlockType.OBS_SHELL

    # No tool name — fall back to content heuristics
    if _ERROR_PATTERNS.search(content[:500]):
        return BlockType.OBS_ERROR

    if _GREP_PATTERNS.search(content[:500]):
        return BlockType.OBS_GREP
    if _FILE_PATTERNS.search(content[:500]):
        return BlockType.OBS_FILE

    return BlockType.OBS_GENERIC


def segment_messages(
    messages: list[dict],
    step: int = 0,
    start_block_id: int = 0,
) -> list[Block]:
    """Parse OpenAI-format messages into typed blocks.

    Each message becomes one block. The caller is responsible for tracking
    block_id continuity across steps (pass start_block_id).

    Message format expected:
        {"role": "system", "content": "..."}
        {"role": "user", "content": "..."}
        {"role": "assistant", "content": "...", "tool_calls": [...]}
        {"role": "tool", "content": "...", "tool_call_id": "...", "name": "..."}
    """
    blocks: list[Block] = []
    block_id = start_block_id

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "system":
            # System prompt — always SYSTEM type
            blocks.append(Block(
                block_id=block_id,
                block_type=BlockType.SYSTEM,
                content=content,
                token_count=count_tokens(content),
                step_created=step,
            ))
            block_id += 1

        elif role == "user":
            # User message — typically the task description
            blocks.append(Block(
                block_id=block_id,
                block_type=BlockType.TASK,
                content=content,
                token_count=count_tokens(content),
                step_created=step,
            ))
            block_id += 1

        elif role == "assistant":
            # Assistant message may contain reasoning (content) + tool calls
            if content.strip():
                blocks.append(Block(
                    block_id=block_id,
                    block_type=BlockType.THOUGHT,
                    content=content,
                    token_count=count_tokens(content),
                    step_created=step,
                ))
                block_id += 1

            # Tool calls in the assistant message → ACTION blocks
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                action_str = f"{fn.get('name', 'unknown')}({fn.get('arguments', '')})"
                blocks.append(Block(
                    block_id=block_id,
                    block_type=BlockType.ACTION,
                    content=action_str,
                    token_count=count_tokens(action_str),
                    step_created=step,
                ))
                block_id += 1

        elif role == "tool":
            # Tool result → classify into observation sub-type
            tool_name = msg.get("name", None)
            block_type = _classify_observation(content, tool_name)
            blocks.append(Block(
                block_id=block_id,
                block_type=block_type,
                content=content,
                token_count=count_tokens(content),
                step_created=step,
            ))
            block_id += 1

    return blocks


def segment_new_step(
    thought: str,
    action: dict | None,
    observation: str | None,
    step: int,
    start_block_id: int,
    tool_name: str | None = None,
) -> list[Block]:
    """Segment a single new agent step (thought + action + observation) into blocks.

    Convenience wrapper for adding a new step without constructing full message dicts.
    """
    blocks: list[Block] = []
    block_id = start_block_id

    if thought.strip():
        blocks.append(Block(
            block_id=block_id,
            block_type=BlockType.THOUGHT,
            content=thought,
            token_count=count_tokens(thought),
            step_created=step,
        ))
        block_id += 1

    if action is not None:
        fn = action.get("function", action)
        action_str = f"{fn.get('name', 'unknown')}({fn.get('arguments', '')})"
        blocks.append(Block(
            block_id=block_id,
            block_type=BlockType.ACTION,
            content=action_str,
            token_count=count_tokens(action_str),
            step_created=step,
        ))
        block_id += 1

    if observation is not None:
        block_type = _classify_observation(observation, tool_name)
        blocks.append(Block(
            block_id=block_id,
            block_type=block_type,
            content=observation,
            token_count=count_tokens(observation),
            step_created=step,
        ))
        block_id += 1

    return blocks
