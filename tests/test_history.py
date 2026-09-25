import unittest

from desk_pilot.agent.history import (
    is_tool_pairing_error,
    normalize_tool_calls,
    sanitize_messages,
    trim_messages,
)


def _assistant(*ids: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "list_ui", "arguments": "{}"}}
            for cid in ids
        ],
    }


def _tool(cid: str, body: str = "{}") -> dict:
    return {"role": "tool", "tool_call_id": cid, "content": body}


class HistoryTests(unittest.TestCase):
    def test_sanitize_drops_orphan_tool_results(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "goal"},
            _tool("call_orphan"),
            _assistant("call_ok"),
            _tool("call_ok"),
        ]
        clean = sanitize_messages(messages)
        roles = [m["role"] for m in clean]
        self.assertEqual(roles, ["system", "user", "assistant", "tool"])
        self.assertEqual(clean[-1]["tool_call_id"], "call_ok")
        self.assertFalse(any(m.get("tool_call_id") == "call_orphan" for m in clean))

    def test_sanitize_stubs_missing_tool_results(self) -> None:
        messages = [{"role": "assistant", "content": None, "tool_calls": _assistant("a", "b")["tool_calls"]}]
        clean = sanitize_messages(messages)
        self.assertEqual(clean[0]["role"], "assistant")
        ids = [m["tool_call_id"] for m in clean[1:]]
        self.assertEqual(ids, ["a", "b"])

    def test_normalize_responses_style_calls(self) -> None:
        raw = [{"type": "function_call", "call_id": "call_9HUX", "name": "type_text", "arguments": "{}"}]
        out = normalize_tool_calls(raw)
        self.assertEqual(out[0]["id"], "call_9HUX")
        self.assertEqual(out[0]["function"]["name"], "type_text")

    def test_trim_does_not_split_tool_groups(self) -> None:
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "g"}]
        for i in range(12):
            cid = f"call_{i}"
            messages.append(_assistant(cid))
            messages.append(_tool(cid))
            messages.append({"role": "user", "content": f"obs {i}"})
        # 2 + 12*3 = 38 messages. Old slice kept last 20 and orphaned a tool result.
        trimmed = trim_messages(messages, max_messages=24)
        self.assertLessEqual(len(trimmed), 24)
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[1]["role"], "user")
        clean = sanitize_messages(trimmed)
        self.assertEqual(len(clean), len(trimmed))
        for i, msg in enumerate(trimmed):
            if msg.get("role") == "tool":
                prev_ids = []
                for earlier in reversed(trimmed[:i]):
                    if earlier.get("role") == "assistant":
                        prev_ids = [c["id"] for c in earlier.get("tool_calls") or []]
                        break
                    if earlier.get("role") != "tool":
                        break
                self.assertIn(msg["tool_call_id"], prev_ids)

    def test_pairing_error_detector(self) -> None:
        self.assertTrue(
            is_tool_pairing_error(
                "OpenRouter HTTP 400: No tool call found for function call output with call_id call_9HUX"
            )
        )
        self.assertFalse(is_tool_pairing_error("OpenRouter HTTP 401: bad key"))


if __name__ == "__main__":
    unittest.main()
