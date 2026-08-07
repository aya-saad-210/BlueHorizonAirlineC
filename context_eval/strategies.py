"""Deterministic context evaluation strategies for airline IROPS transcripts."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

Transcript = Mapping[str, Any]
Message = Mapping[str, Any]


def _get_system_prompt(transcript: Transcript) -> str:
    """Return the configured system prompt or a fallback prompt."""
    system_prompt = transcript.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt.strip():
        return system_prompt.strip()
    return (
        "You are the Blue Horizon Airlines IROPS assistant. Preserve customer "
        "priority, keep context concise, and explain actions clearly."
    )


def _get_messages(transcript: Transcript) -> list[dict[str, Any]]:
    """Return a defensive list of transcript messages."""
    messages = transcript.get("messages", [])
    if not isinstance(messages, list):
        raise TypeError("Transcript messages must be a list.")
    return [dict(message) for message in messages]


def _stringify_content(value: Any) -> str:
    """Convert arbitrary content into a stable string representation."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _truncate_text(text: str, max_chars: int) -> str:
    """Trim a string while preserving the start of the payload."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [{len(text) - max_chars} chars omitted]"


def _serialize_message(message: Message, *, compact: bool = False) -> str:
    """Render a single message into a deterministic text block."""
    role = str(message.get("role", "unknown"))
    content = _stringify_content(message.get("content", ""))

    if role == "assistant":
        tool_calls = message.get("tool_calls") or []
        labels: list[str] = []
        for tool_call in tool_calls:
            name = str(tool_call.get("name", "unknown"))
            arguments = json.dumps(tool_call.get("arguments", {}), sort_keys=True, ensure_ascii=False)
            labels.append(f"{name}({arguments})")
        if compact:
            snippet = _truncate_text(content, 140)
            return f"assistant: {snippet}" if snippet else "assistant"
        if labels:
            return f"assistant: tool_calls=[{'; '.join(labels)}] content={content}"
        return f"assistant: {content}"

    if role == "tool":
        tool_name = str(message.get("tool_name", "tool"))
        if compact:
            return f"tool[{tool_name}]: {_truncate_text(content, 140)}"
        return f"tool[{tool_name}]: {content}"

    if role == "user":
        if compact:
            return f"user: {_truncate_text(content, 160)}"
        return f"user: {content}"

    return f"{role}: {content}"


def _summarize_message(message: Message) -> str:
    """Create a short deterministic summary for a historical message."""
    role = str(message.get("role", "unknown"))
    content = _stringify_content(message.get("content", ""))

    if role == "assistant":
        tool_calls = message.get("tool_calls") or []
        names = [str(tool_call.get("name", "tool")) for tool_call in tool_calls]
        if names:
            return f"assistant calls={','.join(names)}; {content[:140]}"
        return f"assistant; {content[:140]}"

    if role == "tool":
        tool_name = str(message.get("tool_name", "tool"))
        return f"tool:{tool_name}; {_truncate_text(content, 140)}"

    return f"{role}; {_truncate_text(content, 140)}"


def sliding_window(transcript: Transcript, *, window_size: int = 8) -> str:
    """Preserve the system prompt, the first user message, and the latest turns."""
    messages = _get_messages(transcript)
    parts: list[str] = [f"system: {_get_system_prompt(transcript)}"]

    first_user = next((message for message in messages if message.get("role") == "user"), None)
    if first_user is not None:
        parts.append(_serialize_message(first_user, compact=True))

    start_index = max(0, len(messages) - window_size)
    for message in messages[start_index:]:
        parts.append(_serialize_message(message))

    return "\n".join(parts)


def observation_masking(transcript: Transcript, *, max_output_chars: int = 320) -> str:
    """Preserve the conversation while masking oversized tool outputs."""
    messages = _get_messages(transcript)
    parts: list[str] = [f"system: {_get_system_prompt(transcript)}"]

    for message in messages:
        if message.get("role") == "tool":
            content = _stringify_content(message.get("content", ""))
            if len(content) > max_output_chars:
                masked = _truncate_text(content, max_output_chars)
                parts.append(f"tool[{message.get('tool_name', 'tool')}]: {masked}")
            else:
                parts.append(_serialize_message(message))
        else:
            parts.append(_serialize_message(message))

    return "\n".join(parts)


def recursive_summarization(transcript: Transcript, *, keep_latest: int = 4, chunk_size: int = 4) -> str:
    """Summarize historical turns deterministically while preserving the latest turns."""
    messages = _get_messages(transcript)
    parts: list[str] = [f"system: {_get_system_prompt(transcript)}"]

    first_user = next((message for message in messages if message.get("role") == "user"), None)
    if first_user is not None:
        parts.append(_serialize_message(first_user, compact=True))

    if len(messages) > keep_latest:
        old_messages = messages[:-keep_latest]
        for chunk_index, chunk in enumerate(
            [old_messages[index : index + chunk_size] for index in range(0, len(old_messages), chunk_size)],
            start=1,
        ):
            summary = " | ".join(_summarize_message(message) for message in chunk)
            parts.append(f"summary[{chunk_index}]: {summary}")

    for message in messages[-keep_latest:]:
        parts.append(_serialize_message(message))

    return "\n".join(parts)


def zone_based_pruning(
    transcript: Transcript,
    *,
    initial_turns: int = 4,
    recent_turns: int = 4,
) -> str:
    """Keep initial instructions, prune middle tool spam, and retain recent context."""
    messages = _get_messages(transcript)
    parts: list[str] = [f"system: {_get_system_prompt(transcript)}"]

    first_user = next((message for message in messages if message.get("role") == "user"), None)
    if first_user is not None:
        parts.append(_serialize_message(first_user, compact=True))

    for message in messages[:initial_turns]:
        parts.append(_serialize_message(message, compact=True))

    middle_start = initial_turns
    middle_end = max(initial_turns, len(messages) - recent_turns)
    for message in messages[middle_start:middle_end]:
        if message.get("role") == "tool":
            content = _stringify_content(message.get("content", ""))
            parts.append(f"pruned_tool[{message.get('tool_name', 'tool')}]: {_truncate_text(content, 140)}")
        else:
            parts.append(_serialize_message(message, compact=True))

    for message in messages[-recent_turns:]:
        parts.append(_serialize_message(message))

    return "\n".join(parts)


__all__ = [
    "observation_masking",
    "recursive_summarization",
    "sliding_window",
    "zone_based_pruning",
]
