import threading
import time
import unittest
from typing import Any

from desk_pilot.agent.guide import is_guide_goal, snapshot_advanced
from desk_pilot.agent.loop import AgentLoop
from desk_pilot.desktop.mock import MockDesktop
from desk_pilot.desktop.overlay import HighlightOverlay, get_overlay, overlay_wintypes, wndclassw_type
from desk_pilot.desktop.sketch import (
    distance_to_rect_border,
    expand_tiny_rect,
    normalize_rect,
    render_sketch,
    sketch_polyline,
)


class SketchPathTests(unittest.TestCase):
    def test_points_stay_near_rect(self) -> None:
        left, top, right, bottom = 120, 80, 360, 220
        path = sketch_polyline(left, top, right, bottom, amplitude=5, overshoot=3, seed=7)
        self.assertGreaterEqual(len(path), 20)
        self.assertEqual(path[0], path[-1])
        for x, y in path:
            dist = distance_to_rect_border(x, y, left, top, right, bottom)
            self.assertLessEqual(dist, 14)

    def test_double_stroke_differs(self) -> None:
        a = sketch_polyline(10, 10, 80, 50, seed=1, stroke=0)
        b = sketch_polyline(10, 10, 80, 50, seed=1, stroke=1)
        self.assertNotEqual(a, b)

    def test_normalize_swaps_inverted(self) -> None:
        self.assertEqual(normalize_rect([40, 30, 10, 5]), (10, 5, 40, 30))
        self.assertEqual(normalize_rect(None), (0, 0, 0, 0))

    def test_as_rect_accepts_tuple_and_list(self) -> None:
        from desk_pilot.desktop.rects import as_rect

        self.assertEqual(as_rect((40, 30, 10, 5)), [10, 5, 40, 30])
        self.assertEqual(as_rect([200, 100, 340, 180]), [200, 100, 340, 180])
        self.assertIsNone(as_rect(None))
        self.assertIsNone(as_rect([0, 0, 0, 0]))
        self.assertIsNone(as_rect("nope"))

    def test_expand_tiny(self) -> None:
        left, top, right, bottom = expand_tiny_rect(50, 50, 52, 51, min_size=12)
        self.assertGreaterEqual(right - left, 12)
        self.assertGreaterEqual(bottom - top, 12)

    def test_render_is_rgba_with_ink(self) -> None:
        image, origin_x, origin_y = render_sketch([200, 100, 340, 180], seed=3)
        self.assertEqual(image.mode, "RGBA")
        self.assertLess(origin_x, 200)
        self.assertLess(origin_y, 100)
        alpha_max = image.getextrema()[3][1]
        self.assertGreater(alpha_max, 0)


class OverlayNoopTests(unittest.TestCase):
    def test_linux_overlay_is_noop(self) -> None:
        overlay = HighlightOverlay()
        result = overlay.show([10, 10, 80, 40])
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("rect"), [10, 10, 80, 40])
        empty = overlay.show(None)
        self.assertTrue(empty.get("skipped"))
        self.assertIn("no rect", empty.get("error") or "")
        overlay.flash([10, 10, 80, 40], duration=1.0)
        overlay.hide()
        overlay.close()
        get_overlay().flash(None)

    def test_wndclass_fields_resolve_without_hcursor(self) -> None:
        import ctypes
        from types import SimpleNamespace

        sparse = SimpleNamespace(
            UINT=ctypes.c_uint,
            HANDLE=ctypes.c_void_p,
            HWND=ctypes.c_void_p,
            HINSTANCE=ctypes.c_void_p,
            LPCWSTR=ctypes.c_wchar_p,
            DWORD=ctypes.c_ulong,
            WORD=ctypes.c_ushort,
            BOOL=ctypes.c_int,
            WPARAM=ctypes.c_size_t,
            LPARAM=ctypes.c_ssize_t,
            LPVOID=ctypes.c_void_p,
        )
        # Mimic the live Windows failure: HCURSOR / HICON / HBRUSH are absent.
        with self.assertRaises(AttributeError):
            sparse.HCURSOR  # noqa: B018 — attribute must be missing
        aliases = overlay_wintypes(sparse)
        self.assertIs(aliases.HCURSOR, ctypes.c_void_p)
        self.assertIs(aliases.HICON, ctypes.c_void_p)
        self.assertIs(aliases.HBRUSH, ctypes.c_void_p)
        cls = wndclassw_type(sparse)
        names = [item[0] for item in cls._fields_]
        self.assertEqual(
            names,
            [
                "style",
                "lpfnWndProc",
                "cbClsExtra",
                "cbWndExtra",
                "hInstance",
                "hIcon",
                "hCursor",
                "hbrBackground",
                "lpszMenuName",
                "lpszClassName",
            ],
        )
        wnd = cls()
        self.assertEqual(wnd.style, 0)

    def test_real_wintypes_alias_helper_never_raises(self) -> None:
        from ctypes import wintypes

        aliases = overlay_wintypes()
        self.assertTrue(hasattr(aliases, "HCURSOR"))
        cls = wndclassw_type()
        self.assertTrue(cls._fields_)
        # Direct access still fails on this Python; the helper must not.
        if not hasattr(wintypes, "HCURSOR"):
            with self.assertRaises(AttributeError):
                _ = wintypes.HCURSOR


class GuideIntentTests(unittest.TestCase):
    def test_keywords(self) -> None:
        self.assertTrue(is_guide_goal("how to open Helium and search for a dank meme"))
        self.assertTrue(is_guide_goal("Show me how to use Notepad"))
        self.assertTrue(is_guide_goal("Guide me through opening Chrome"))
        self.assertTrue(is_guide_goal("Teach me to search Google"))
        self.assertTrue(is_guide_goal("Walk me through Start menu"))
        self.assertTrue(is_guide_goal("How do I open Helium?"))
        self.assertFalse(is_guide_goal("open Helium and search for a dank meme"))
        self.assertFalse(is_guide_goal("Open Notepad and type hello"))
        self.assertFalse(is_guide_goal("however you like, open notepad"))

    def test_force_toggle(self) -> None:
        self.assertTrue(is_guide_goal("open Helium", force=True))
        self.assertFalse(is_guide_goal("", force=False))


class ScriptedLLM:
    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.calls = 0
        self.tool_names: list[str] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        self.tool_names = [item["function"]["name"] for item in tools]
        if not self.script:
            raise AssertionError("LLM script exhausted")
        return self.script.pop(0)


def _call(name: str, arguments: str, call_id: str = "c1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class GuideLoopTests(unittest.TestCase):
    def test_auto_path_never_sketches(self) -> None:
        from tests.test_loop import ScriptedLLM as LoopLLM, _call as loop_call

        llm = LoopLLM(
            [
                {"content": "", "tool_calls": [loop_call("launch_app", '{"name":"notepad"}', "1")]},
                {"content": "", "tool_calls": [loop_call("done", '{"result":"opened"}', "2")]},
            ]
        )
        desk = MockDesktop()
        logs: list[tuple[str, str]] = []
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=5,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("Open Notepad")
        self.assertEqual(result.status, "done")
        self.assertEqual(desk.highlights, [])
        self.assertIsNone(desk.highlight_visible)
        self.assertTrue(any(a.startswith("launch_app") for a in desk.actions))
        self.assertFalse(any(k == "sketch" for k, _ in logs))

    def test_guide_sketches_and_does_not_click(self) -> None:
        waits: list[str] = []

        def await_step() -> str:
            waits.append("wait")
            return "continue"

        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call(
                            "guide_step",
                            '{"instruction":"Click Start","name":"Start"}',
                            "1",
                        )
                    ],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"You opened Start"}', "2")]},
            ]
        )
        desk = MockDesktop()
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=6,
            force_guide=True,
            await_step=await_step,
        ).run("how to open Start")
        self.assertEqual(result.status, "done")
        self.assertEqual(waits, ["wait"])
        self.assertEqual(len(desk.highlights), 1)
        self.assertFalse(any(a.startswith("click") for a in desk.actions))
        self.assertIn("guide_step", llm.tool_names)
        self.assertNotIn("click", llm.tool_names)
        left, top, right, bottom = desk.highlights[0]
        self.assertLess(left, right)
        self.assertIsNone(desk.highlight_visible)

    def test_instruction_only_skips_sketch_and_does_not_wait(self) -> None:
        waits: list[str] = []
        logs: list[tuple[str, str]] = []

        def await_step() -> str:
            waits.append("wait")
            return "continue"

        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [_call("guide_step", '{"instruction":"Click Start"}', "1")],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"retried"}', "2")]},
            ]
        )
        desk = MockDesktop()
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=6,
            force_guide=True,
            await_step=await_step,
            on_log=lambda k, m: logs.append((k, m)),
        ).run("how to click Start")
        self.assertEqual(result.status, "done")
        self.assertEqual(waits, [])
        self.assertEqual(desk.highlights, [])
        self.assertTrue(
            any(k == "sketch" and "skipped: no rect (need name/automation_id/xy)" in m for k, m in logs)
        )

    def test_failed_blit_logs_sketch_failed(self) -> None:
        waits: list[str] = []
        logs: list[tuple[str, str]] = []

        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call("guide_step", '{"instruction":"Click Start","name":"Start"}', "1")
                    ],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"ok"}', "2")]},
            ]
        )
        desk = MockDesktop()
        desk.fail_highlight = True
        desk.fail_highlight_error = "UpdateLayeredWindow failed (GetLastError=87)"
        result = AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=6,
            force_guide=True,
            await_step=lambda: waits.append("wait") or "continue",
            on_log=lambda k, m: logs.append((k, m)),
        ).run("how to click Start")
        self.assertEqual(result.status, "done")
        self.assertEqual(waits, ["wait"])
        self.assertEqual(desk.highlights, [])
        self.assertTrue(any(k == "sketch" and m.startswith("failed:") for k, m in logs))
        self.assertTrue(any("GetLastError=87" in m for _, m in logs))

    def test_guide_name_resolves_rect(self) -> None:
        logs: list[tuple[str, str]] = []
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call("guide_step", '{"instruction":"Click Start","name":"Start"}', "1")
                    ],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"clicked"}', "2")]},
            ]
        )
        desk = MockDesktop()
        AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=5,
            force_guide=True,
            await_step=lambda: "continue",
            on_log=lambda k, m: logs.append((k, m)),
        ).run("guide me through Start")
        self.assertEqual(desk.highlights, [[0, 1040, 48, 1080]])
        self.assertTrue(any(k == "sketch" and "[0, 1040, 48, 1080]" in m for k, m in logs))
        self.assertFalse(any("skipped:" in m or m.startswith("failed:") for k, m in logs))

    def test_mock_highlight_accepts_tuple(self) -> None:
        desk = MockDesktop()
        result = desk.show_highlight((10, 20, 40, 60))
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("rect"), [10, 20, 40, 60])
        self.assertEqual(desk.highlights, [[10, 20, 40, 60]])
        desk.hide_highlight()
        self.assertIsNone(desk.highlight_visible)

    def test_missing_name_falls_back_to_window(self) -> None:
        logs: list[tuple[str, str]] = []
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call(
                            "guide_step",
                            '{"instruction":"Click the mystery button","name":"NoSuchControl"}',
                            "1",
                        )
                    ],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"ok"}', "2")]},
            ]
        )
        desk = MockDesktop()
        hints: list[str] = []
        AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=5,
            force_guide=True,
            await_step=lambda: "continue",
            on_log=lambda k, m: logs.append((k, m)),
            on_guide_step=lambda text: hints.append(text),
        ).run("how to click a missing control")
        self.assertEqual(len(desk.highlights), 1)
        self.assertEqual(desk.highlights[0], [0, 0, 1920, 1080])
        self.assertTrue(any("window fallback" in m for k, m in logs if k == "sketch"))
        self.assertTrue(any("whole window is highlighted as a fallback" in t for t in hints if t))

    def test_how_to_goal_enables_guide_without_toggle(self) -> None:
        llm = ScriptedLLM(
            [{"content": "", "tool_calls": [_call("done", '{"result":"ok"}', "z")]}]
        )
        loop = AgentLoop(backend=MockDesktop(), llm=llm, max_steps=3, await_step=lambda: "continue")
        result = loop.run("how to open Helium and search for a dank meme")
        self.assertTrue(loop.guide_mode)
        self.assertEqual(result.status, "done")

    def test_intercepted_click_does_not_act(self) -> None:
        llm = ScriptedLLM(
            [
                {"content": "", "tool_calls": [_call("click", '{"name":"Start"}', "1")]},
                {"content": "", "tool_calls": [_call("done", '{"result":"guided"}', "2")]},
            ]
        )
        desk = MockDesktop()
        AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=5,
            force_guide=True,
            await_step=lambda: "continue",
        ).run("teach me the Start menu")
        self.assertFalse(any(a.startswith("click") for a in desk.actions))
        self.assertEqual(desk.window_title, "Desktop")
        self.assertEqual(len(desk.highlights), 1)

    def test_continue_event_unblocks(self) -> None:
        event = threading.Event()

        def later() -> None:
            time.sleep(0.05)
            event.set()

        threading.Thread(target=later, daemon=True).start()
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [_call("guide_step", '{"instruction":"Click Start","name":"Start"}', "1")],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"unblocked"}', "2")]},
            ]
        )
        result = AgentLoop(
            backend=MockDesktop(),
            llm=llm,
            max_steps=5,
            force_guide=True,
            continue_event=event,
        ).run("guide me")
        self.assertEqual(result.status, "done")
        self.assertEqual(result.message, "unblocked")

    def test_stop_during_wait(self) -> None:
        stop = threading.Event()
        cont = threading.Event()

        def later() -> None:
            time.sleep(0.05)
            stop.set()

        threading.Thread(target=later, daemon=True).start()
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [_call("guide_step", '{"instruction":"Click Start","name":"Start"}', "1")],
                }
            ]
        )
        result = AgentLoop(
            backend=MockDesktop(),
            llm=llm,
            max_steps=4,
            force_guide=True,
            stop_event=stop,
            continue_event=cont,
        ).run("how to click Start")
        self.assertEqual(result.status, "stopped")

    def test_snapshot_advanced(self) -> None:
        before = {"window": {"name": "Desktop"}}
        after = {"window": {"name": "Untitled - Notepad"}}
        self.assertTrue(snapshot_advanced(before, after, {"title_contains": "Notepad"}))
        self.assertFalse(snapshot_advanced(before, before, {"title_contains": "Notepad"}))


if __name__ == "__main__":
    unittest.main()
