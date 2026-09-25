import unittest
from typing import Any

from desk_pilot.agent.loop import AgentLoop
from desk_pilot.desktop.mock import MockDesktop


class ScriptedLLM:
    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        if not self.script:
            raise AssertionError("LLM script exhausted")
        return self.script.pop(0)


def _call(name: str, arguments: str, call_id: str = "c1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class AgentLoopTests(unittest.TestCase):
    def test_notepad_goal_with_scripted_tools(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("hotkey", '{"keys":"win+r"}', "1")]},
                {"content": "", "tool_calls": [_call("type_text", '{"text":"notepad"}', "2")]},
                {"content": "", "tool_calls": [_call("hotkey", '{"keys":"enter"}', "3")]},
                {"content": "", "tool_calls": [_call("wait_for_window", '{"title_contains":"Notepad"}', "4")]},
                {"content": "", "tool_calls": [_call("type_text", '{"text":"hello"}', "5")]},
                {"content": "", "tool_calls": [_call("done", '{"result":"Notepad shows hello"}', "6")]},
            ]
        )
        logs: list[tuple[str, str]] = []
        desk = MockDesktop()
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=10,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("Open Notepad and type hello")
        self.assertEqual(result.status, "done")
        self.assertIn("hello", result.message.lower() + desk.edit_text)
        self.assertEqual(desk.edit_text, "hello")
        self.assertTrue(any(k == "done" for k, _ in logs))

    def test_empty_goal_fails_fast(self) -> None:
        result = AgentLoop(backend=MockDesktop(), llm=ScriptedLLM([]), max_steps=3).run("  ")
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.steps, 0)

    def test_fail_tool_ends_run(self) -> None:
        llm = ScriptedLLM(
            [{"content": "", "tool_calls": [_call("fail", '{"reason":"Start menu missing"}', "x")]}]
        )
        result = AgentLoop(backend=MockDesktop(), llm=llm, max_steps=5).run("Click Start")
        self.assertEqual(result.status, "fail")
        self.assertIn("Start menu", result.message)

    def test_step_budget(self) -> None:
        llm = ScriptedLLM(
            [{"content": "", "tool_calls": [_call("list_ui", "{}", str(i))]} for i in range(3)]
        )
        result = AgentLoop(backend=MockDesktop(), llm=llm, max_steps=3).run("Do nothing forever")
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.steps, 3)
        self.assertIn("budget", result.message.lower())

    def test_observe_log_surfaces_com_error(self) -> None:
        class BrokenDesktop(MockDesktop):
            def list_ui(self, max_depth: int = 5, max_controls: int = 70) -> dict[str, Any]:
                return {
                    "ok": False,
                    "com_error": True,
                    "error": "CoInitialize has not been called.",
                    "window": {"name": ""},
                    "controls": [],
                }

        logs: list[tuple[str, str]] = []
        llm = ScriptedLLM(
            [{"content": "", "tool_calls": [_call("fail", '{"reason":"COM down"}', "x")]}]
        )
        AgentLoop(
            backend=BrokenDesktop(),
            llm=llm,
            max_steps=3,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("Click Start")
        observe = [message for kind, message in logs if kind == "observe"]
        self.assertTrue(any("COM ERROR" in message for message in observe))

    def test_launch_app_recovers_from_win_r_miss(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("hotkey", '{"keys":"win+r"}', "1")]},
                {"content": "", "tool_calls": [_call("type_text", '{"text":"helium"}', "2")]},
                {"content": "", "tool_calls": [_call("hotkey", '{"keys":"enter"}', "3")]},
                {
                    "content": "",
                    "tool_calls": [_call("wait_for_window", '{"title_contains":"Helium","timeout_seconds":0.2}', "4")],
                },
                {"content": "", "tool_calls": [_call("click", '{"name":"OK"}', "5")]},
                {"content": "", "tool_calls": [_call("launch_app", '{"name":"notepad"}', "6")]},
                {"content": "", "tool_calls": [_call("done", '{"result":"Opened Notepad after launch_app"}', "7")]},
            ]
        )
        desk = MockDesktop()
        result = AgentLoop(backend=desk, llm=llm, max_steps=10).run("Open helium then notepad")
        self.assertEqual(result.status, "done")
        self.assertEqual(desk.edit_text, "")
        self.assertIn("Notepad", desk.window_title)


if __name__ == "__main__":
    unittest.main()
