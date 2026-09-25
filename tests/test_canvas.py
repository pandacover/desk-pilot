import unittest
from pathlib import Path
from typing import Any

from desk_pilot.agent.canvas import (
    art_subject,
    is_art_goal,
    is_thin_tree,
    should_use_canvas_mode,
    stroke_playbook,
)
from desk_pilot.agent.guide import is_guide_goal
from desk_pilot.agent.loop import AgentLoop
from desk_pilot.agent.prompts import CANVAS_SYSTEM_PROMPT
from desk_pilot.agent.tools import AUTO_ONLY_TOOLS, dispatch_tool
from desk_pilot.desktop.mock import MockDesktop


class CanvasDetectTests(unittest.TestCase):
    def test_art_goals(self) -> None:
        self.assertTrue(is_art_goal("open tldraw and sketch me a car"))
        self.assertTrue(is_art_goal("draw me a cat"))
        self.assertTrue(is_art_goal("paint a house in Figma"))
        self.assertTrue(is_art_goal("open tldraw"))
        self.assertFalse(is_art_goal("Open Notepad and type hello"))
        self.assertFalse(is_art_goal("open Helium and search for a dank meme"))

    def test_art_subject(self) -> None:
        self.assertEqual(art_subject("open tldraw and sketch me a car"), "car")
        self.assertEqual(art_subject("draw me a red bicycle"), "red bicycle")
        self.assertNotEqual(art_subject("open tldraw"), "aw")

    def test_sketch_me_a_car_is_not_guide(self) -> None:
        self.assertFalse(is_guide_goal("open tldraw and sketch me a car"))
        self.assertFalse(is_guide_goal("sketch me a car"))
        self.assertFalse(is_guide_goal("draw me a car"))
        self.assertTrue(is_guide_goal("how to sketch a car"))

    def test_thin_tldraw_tree_enables_canvas(self) -> None:
        desk = MockDesktop()
        desk._open_tldraw()
        tree = desk.list_ui()
        self.assertLessEqual(len(tree["controls"]), 10)
        self.assertTrue(is_thin_tree(tree))
        self.assertTrue(should_use_canvas_mode("click around", tree))
        self.assertTrue(should_use_canvas_mode("open tldraw and sketch me a car", tree))

    def test_car_playbook_has_body_and_wheels(self) -> None:
        strokes = stroke_playbook("car", [80, 40, 1280, 800])
        names = [s["name"] for s in strokes]
        self.assertEqual(names, ["body", "cabin", "wheel_rear", "wheel_front"])
        for stroke in strokes:
            self.assertGreaterEqual(len(stroke["points"]), 5)

    def test_drag_path_polyline(self) -> None:
        from desk_pilot.desktop.drag import build_drag_path

        path = build_drag_path(0, 0, 10, 0, [[0, 0], [10, 0], [10, 10]])
        self.assertEqual(path[0], (0, 0))
        self.assertEqual(path[-1], (10, 10))
        self.assertGreater(len(path), 3)


class ScriptedLLM:
    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls = 0
        self.tool_names: list[str] = []
        self.systems: list[str] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        self.tool_names = [item["function"]["name"] for item in tools]
        if messages and messages[0].get("role") == "system":
            self.systems.append(str(messages[0].get("content") or ""))
        if not self.script:
            raise AssertionError("LLM script exhausted")
        return self.script.pop(0)

    def generate_image(self, prompt: str) -> dict[str, Any]:
        return {"ok": False, "error": "test stub: image gen unavailable"}


def _call(name: str, arguments: str, call_id: str = "c1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class CanvasLoopTests(unittest.TestCase):
    def test_art_goal_uses_canvas_prompt_and_drag_tool(self) -> None:
        llm = ScriptedLLM(
            [{"content": "", "tool_calls": [_call("done", '{"result":"ok"}', "z")]}]
        )
        loop = AgentLoop(backend=MockDesktop(), llm=llm, max_steps=3)
        result = loop.run("open tldraw and sketch me a car")
        self.assertEqual(result.status, "done")
        self.assertTrue(loop.canvas_mode)
        self.assertFalse(loop.guide_mode)
        self.assertIn("drag", llm.tool_names)
        self.assertIn("prepare_art", llm.tool_names)
        self.assertNotIn("guide_step", llm.tool_names)
        self.assertTrue(any("CANVAS mode" in text for text in llm.systems))
        self.assertIn("CANVAS mode", CANVAS_SYSTEM_PROMPT)

    def test_multi_drag_in_one_turn(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("launch_app", '{"name":"tldraw"}', "1")]},
                {
                    "content": "",
                    "tool_calls": [
                        _call("drag", '{"x1":200,"y1":300,"x2":700,"y2":300,"points":[[200,300],[700,300],[700,420],[200,420],[200,300]]}', "a"),
                        _call("drag", '{"x1":240,"y1":430,"x2":300,"y2":430,"points":[[260,450],[280,470],[260,490],[240,470],[260,450]]}', "b"),
                        _call("drag", '{"x1":540,"y1":430,"x2":600,"y2":430,"points":[[560,450],[580,470],[560,490],[540,470],[560,450]]}', "c"),
                    ],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"sketched a car"}', "z")]},
            ]
        )
        desk = MockDesktop()
        result = AgentLoop(backend=desk, llm=llm, max_steps=8).run("open tldraw and sketch me a car")
        self.assertEqual(result.status, "done")
        self.assertEqual(len(desk.drags), 3)
        self.assertIn("tldraw", desk.window_title.lower())
        tree = desk.list_ui()
        self.assertLessEqual(len(tree["controls"]), 10)

    def test_prepare_art_then_paste(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("launch_app", '{"name":"tldraw"}', "1")]},
                {"content": "", "tool_calls": [_call("prepare_art", '{"subject":"car"}', "2")]},
                {"content": "", "tool_calls": [_call("hotkey", '{"keys":"ctrl+v"}', "3")]},
                {"content": "", "tool_calls": [_call("done", '{"result":"pasted car"}', "z")]},
            ]
        )
        desk = MockDesktop()
        result = AgentLoop(backend=desk, llm=llm, max_steps=8).run("sketch me a car")
        self.assertEqual(result.status, "done")
        self.assertTrue(desk.clipboard_image)
        self.assertTrue(Path(desk.clipboard_image).is_file())
        self.assertTrue(any(a.startswith("paste ") for a in desk.actions))

    def test_focus_guard_restores_tldraw(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("launch_app", '{"name":"tldraw"}', "1")]},
                {"content": "", "tool_calls": [_call("click", '{"name":"Back"}', "2")]},
                {"content": "", "tool_calls": [_call("done", '{"result":"still on tldraw"}', "z")]},
            ]
        )
        desk = MockDesktop()
        desk.steal_focus_after_action = True
        logs: list[tuple[str, str]] = []
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=8,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("open tldraw and sketch me a car")
        self.assertEqual(result.status, "done")
        self.assertIn("tldraw", desk.window_title.lower())
        self.assertNotEqual(desk.window_title, "Desk Pilot")
        self.assertTrue(any("focus guard" in m for k, m in logs if k == "act"))

    def test_guide_rejects_drag(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("drag", '{"x1":1,"y1":1,"x2":2,"y2":2}', "1")]},
                {"content": "", "tool_calls": [_call("done", '{"result":"guided"}', "z")]},
            ]
        )
        desk = MockDesktop()
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=5,
            force_guide=True,
            await_step=lambda: "continue",
        ).run("how to sketch a car")
        self.assertEqual(result.status, "done")
        self.assertEqual(desk.drags, [])
        self.assertTrue(result)
        self.assertIn("drag", AUTO_ONLY_TOOLS)

    def test_dispatch_prepare_art_without_llm(self) -> None:
        desk = MockDesktop()
        result = dispatch_tool(desk, "prepare_art", {"subject": "car"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "geometric")
        self.assertTrue(result["generate_error"])
        self.assertFalse(result["clipboard"])


class ClipboardStubTests(unittest.TestCase):
    def test_linux_clipboard_is_clear_fail(self) -> None:
        from desk_pilot.desktop.clipboard import copy_png_to_clipboard

        desk = MockDesktop()
        art = dispatch_tool(desk, "prepare_art", {"subject": "car"})
        clip = copy_png_to_clipboard(art["path"])
        self.assertFalse(clip["ok"])
        self.assertTrue(clip.get("stub"))
        self.assertIn("Windows-only", clip.get("error") or "")


if __name__ == "__main__":
    unittest.main()
