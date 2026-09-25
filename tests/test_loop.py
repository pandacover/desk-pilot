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
        self.assertEqual(desk.highlights, [])

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

    def test_launch_app_skips_second_instance(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("launch_app", '{"name":"notepad"}', "1")]},
                {"content": "", "tool_calls": [_call("type_text", '{"text":"keep me"}', "2")]},
                {"content": "", "tool_calls": [_call("launch_app", '{"name":"Notepad"}', "3")]},
                {"content": "", "tool_calls": [_call("done", '{"result":"reused notepad"}', "4")]},
            ]
        )
        desk = MockDesktop()
        result = AgentLoop(backend=desk, llm=llm, max_steps=8).run("Open Notepad")
        self.assertEqual(result.status, "done")
        self.assertEqual(desk.edit_text, "keep me")
        launches = [a for a in desk.actions if a.startswith("launch_app")]
        self.assertEqual(len(launches), 2)

    def test_pairing_error_resets_history_and_continues(self) -> None:
        from desk_pilot.llm.openrouter import LLMError

        class FlakyLLM:
            def __init__(self) -> None:
                self.calls = 0
                self.payloads: list[list[dict[str, Any]]] = []

            def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
                self.calls += 1
                self.payloads.append(messages)
                if self.calls == 1:
                    raise LLMError(
                        "OpenRouter HTTP 400: No tool call found for function call output "
                        "with call_id call_9HUXjgVneTm5MLPQXibJnRf6."
                    )
                return {"content": "", "tool_calls": [_call("done", '{"result":"recovered"}', "z")]}

        llm = FlakyLLM()
        result = AgentLoop(backend=MockDesktop(), llm=llm, max_steps=5).run("Click Start")
        self.assertEqual(result.status, "done")
        self.assertEqual(result.message, "recovered")
        self.assertEqual(llm.calls, 2)
        second = llm.payloads[1]
        self.assertEqual(second[0]["role"], "system")
        self.assertTrue(all(m.get("role") != "tool" for m in second))

    def test_find_files_scripted_goal(self) -> None:
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [_call("find_files", '{"name":"brawlhalla.exe"}', "1")],
                },
                {
                    "content": "",
                    "tool_calls": [
                        _call(
                            "done",
                            '{"result":"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Brawlhalla\\\\Brawlhalla.exe"}',
                            "2",
                        )
                    ],
                },
            ]
        )
        desk = MockDesktop()
        logs: list[tuple[str, str]] = []
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=6,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("find brawlhalla.exe on my computer")
        self.assertEqual(result.status, "done")
        self.assertIn("brawlhalla.exe", result.message.lower())
        self.assertFalse(any("win+s" in m.lower() for _, m in logs))
        self.assertTrue(any("find_files" in m for k, m in logs if k == "act"))

    def test_stale_browser_nav_is_not_success(self) -> None:
        class RecordingLLM(ScriptedLLM):
            def __init__(self, script):
                super().__init__(script)
                self.payloads: list[list[dict[str, Any]]] = []

            def complete(self, messages, tools):
                self.payloads.append(messages)
                return super().complete(messages, tools)

        llm = RecordingLLM(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call("type_text", '{"text":"https://www.google.com/imghp"}', "1"),
                        _call("hotkey", '{"keys":"enter"}', "2"),
                    ],
                },
                {
                    "content": "",
                    "tool_calls": [_call("fail", '{"reason":"navigation did not change the title"}', "3")],
                },
            ]
        )
        desk = MockDesktop()
        desk._open_tldraw()
        desk.window_title = "brawlhalla.exe - Helium"
        desk.stale_nav = True
        logs: list[tuple[str, str]] = []
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=8,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("open google images")
        self.assertEqual(result.status, "fail")
        self.assertEqual(desk.window_title, "brawlhalla.exe - Helium")
        self.assertTrue(any("NAV failed" in m or "nav_failed" in m.lower() or "stale" in m.lower() for _, m in logs))
        self.assertTrue(any("ctrl+l" in a for a in desk.actions))
        tool_blobs = " ".join(
            str(m.get("content") or "") for payload in llm.payloads for m in payload if m.get("role") == "tool"
        )
        self.assertIn("nav_failed", tool_blobs)

    def test_navigate_tool_is_one_act(self) -> None:
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [_call("navigate", '{"url":"https://images.google.com"}', "1")],
                },
                {
                    "content": "",
                    "tool_calls": [_call("done", '{"result":"opened the page"}', "2")],
                },
            ]
        )
        desk = MockDesktop()
        desk._open_tldraw()
        logs: list[tuple[str, str]] = []
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=6,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("open a URL in Helium")
        self.assertEqual(result.status, "done")
        self.assertIn("google", desk.window_title.lower())
        acts = [m for k, m in logs if k == "act"]
        self.assertTrue(any(m.startswith("navigate ") for m in acts))
        self.assertFalse(any("ctrl+l" in m.lower() for m in acts))

    def test_stuck_loop_forces_strategy_change(self) -> None:
        class RecordingLLM(ScriptedLLM):
            def __init__(self, script):
                super().__init__(script)
                self.payloads: list[list[dict[str, Any]]] = []

            def complete(self, messages, tools):
                self.payloads.append(messages)
                return super().complete(messages, tools)

        llm = RecordingLLM(
            [
                {"content": "", "tool_calls": [_call("click", '{"name":"No Such Button"}', "1")]},
                {"content": "", "tool_calls": [_call("click", '{"name":"No Such Button"}', "2")]},
                {"content": "", "tool_calls": [_call("click", '{"name":"No Such Button"}', "3")]},
                {"content": "", "tool_calls": [_call("fail", '{"reason":"search box never focused"}', "4")]},
            ]
        )
        result = AgentLoop(backend=MockDesktop(), llm=llm, max_steps=8).run("find brawlhalla.exe on my computer")
        self.assertEqual(result.status, "fail")
        fourth = llm.payloads[3]
        user_text = " ".join(str(m.get("content") or "") for m in fourth if m.get("role") == "user")
        self.assertIn("STUCK", user_text)
        self.assertIn("find_files", user_text)

    def test_ctrl_s_image_goal_warns(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("hotkey", '{"keys":"ctrl+s"}', "1")]},
                {
                    "content": "",
                    "tool_calls": [_call("fail", '{"reason":"ctrl+s would save HTML"}', "2")],
                },
            ]
        )
        logs: list[tuple[str, str]] = []
        result = AgentLoop(
            backend=MockDesktop(),
            llm=llm,
            max_steps=6,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("download a picture of a cat")
        self.assertEqual(result.status, "fail")
        self.assertTrue(any("html" in m.lower() for _, m in logs))


if __name__ == "__main__":
    unittest.main()
