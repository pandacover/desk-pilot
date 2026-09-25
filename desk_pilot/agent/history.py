"""Keep Chat Completions tool_calls paired with matching tool results.

OpenRouter / Azure-OpenAI rejects a request when a `role=tool` message's
`tool_call_id` (Responses API: `call_id`) has no assistant `tool_calls` entry.
A naive sliding-window trim can drop the assistant turn and keep the results.
"""

from __future__ import annotations

from typing import Any


def is_tool_pairing_error(exc: BaseException | str) -> bool:
    text = str(exc or "").lower()
    if "no tool call found for function call output" in text:
        return True
    if "function call output" in text and ("call_id" in text or "tool_call_id" in text):
        return True
    return False


def tool_call_id(call: dict[str, Any] | None) -> str:
    if not isinstance(call, dict):
        return ""
    return str(call.get("id") or call.get("call_id") or "").strip()


def normalize_tool_calls(tool_calls: list[Any] | None) -> list[dict[str, Any]]:
    """Chat Completions shape, with a stable id on every call."""
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(tool_calls or []):
        if not isinstance(raw, dict):
            continue
        call = dict(raw)
        fn = call.get("function")
        if not isinstance(fn, dict):
            fn = {
                "name": str(call.get("name") or ""),
                "arguments": call.get("arguments") if isinstance(call.get("arguments"), str) else "{}",
            }
        else:
            fn = {
                "name": str(fn.get("name") or ""),
                "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str) else "{}",
            }
        cid = tool_call_id(call) or f"call_{index}_{fn.get('name') or 'tool'}"
        out.append({"id": cid, "type": "function", "function": fn})
    return out


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop orphan tool results; stub missing results; keep valid turns."""
    sanitized: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")
        if role == "tool":
            # Orphan result with no preceding assistant tool_calls.
            i += 1
            continue
        if role != "assistant" or not msg.get("tool_calls"):
            sanitized.append(msg)
            i += 1
            continue
        calls = normalize_tool_calls(list(msg.get("tool_calls") or []))
        i += 1
        results: list[dict[str, Any]] = []
        while i < len(messages) and messages[i].get("role") == "tool":
            results.append(messages[i])
            i += 1
        if not calls:
            continue
        by_id = {tool_call_id(c): c for c in calls if tool_call_id(c)}
        kept_results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in results:
            rid = str(result.get("tool_call_id") or result.get("call_id") or "").strip()
            if rid and rid in by_id and rid not in seen:
                kept_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": rid,
                        "content": str(result.get("content") or ""),
                    }
                )
                seen.add(rid)
        for call in calls:
            cid = tool_call_id(call)
            if cid and cid not in seen:
                kept_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": cid,
                        "content": '{"ok":false,"error":"tool result omitted during history repair"}',
                    }
                )
                seen.add(cid)
        assistant = {
            "role": "assistant",
            "content": msg.get("content") if msg.get("content") else None,
            "tool_calls": calls,
        }
        sanitized.append(assistant)
        sanitized.extend(kept_results)
    return sanitized


def trim_messages(messages: list[dict[str, Any]], *, max_messages: int = 24) -> list[dict[str, Any]]:
    """Keep system + first user, then complete trailing turns (never split tool groups)."""
    if len(messages) <= max_messages:
        return list(messages)
    groups = _turn_groups(messages)
    if not groups:
        return list(messages)
    head: list[list[dict[str, Any]]] = []
    if groups and groups[0] and groups[0][0].get("role") == "system":
        head.append(groups[0])
        groups = groups[1:]
    if groups and groups[0] and groups[0][0].get("role") == "user":
        head.append(groups[0])
        groups = groups[1:]
    head_flat = [msg for group in head for msg in group]
    budget = max(8, max_messages - len(head_flat))
    kept: list[dict[str, Any]] = []
    for group in reversed(groups):
        if len(group) + len(kept) > budget and kept:
            break
        kept = group + kept
    return head_flat + kept


def _turn_groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        role = msg.get("role")
        if role == "system":
            groups.append([msg])
            i += 1
            continue
        if role == "assistant":
            group = [msg]
            i += 1
            if msg.get("tool_calls"):
                while i < n and messages[i].get("role") == "tool":
                    group.append(messages[i])
                    i += 1
            while i < n and messages[i].get("role") == "user":
                group.append(messages[i])
                i += 1
            groups.append(group)
            continue
        groups.append([msg])
        i += 1
    return groups
