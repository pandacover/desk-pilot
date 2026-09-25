import threading
import time
import unittest
from typing import Any

from desk_pilot.agent.guide import is_guide_goal, snapshot_advanced
from desk_pilot.agent.loop import AgentLoop
from desk_pilot.desktop.mock import MockDesktop
from desk_pilot.desktop.overlay import (
    HighlightOverlay,
    apply_overlay_argtypes,
    coerce_win_handle,
    get_overlay,
    hwnd_topmost_handle,
    overlay_api_argtypes,
    overlay_wintypes,
    wndclassw_type,
)
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
        # Geometric coerce still accepts the (0,0) pad; sketchable_rect rejects it.
        self.assertEqual(as_rect([-12, -12, 12, 12]), [-12, -12, 12, 12])

    def test_zero_zero_xy_is_not_sketchable(self) -> None:
        from desk_pilot.desktop.rects import (
            SKIP_ORIGIN_PAD,
            is_placeholder_origin_rect,
            rect_skip_reason,
            sketchable_rect,
            usable_screen_point,
            xy_pad_rect,
        )

        self.assertFalse(usable_screen_point(0, 0))
        self.assertFalse(usable_screen_point("0", "0"))
        self.assertTrue(usable_screen_point(240, 180))
        self.assertIsNone(xy_pad_rect(0, 0, 12))
        self.assertEqual(xy_pad_rect(240, 180, 12), [228, 168, 252, 192])
        self.assertTrue(is_placeholder_origin_rect([-12, -12, 12, 12]))
        self.assertTrue(is_placeholder_origin_rect([-10, -10, 10, 10]))
        self.assertFalse(is_placeholder_origin_rect([0, 0, 1920, 1080]))
        self.assertFalse(is_placeholder_origin_rect([0, 1040, 48, 1080]))
        self.assertIsNone(sketchable_rect([-12, -12, 12, 12]))
        self.assertEqual(rect_skip_reason([-12, -12, 12, 12]), SKIP_ORIGIN_PAD)
        self.assertIsNone(sketchable_rect([9_999_999, 0, 10_000_010, 40]))
        desk = MockDesktop()
        self.assertIsNone(desk.find_control_rect(x=0, y=0))
        origin = desk.show_highlight([-12, -12, 12, 12])
        self.assertTrue(origin.get("skipped"))
        self.assertIn("origin", (origin.get("error") or "").lower())
        linux = HighlightOverlay().show([-12, -12, 12, 12])
        self.assertTrue(linux.get("skipped"))
        self.assertIn("origin", (linux.get("error") or "").lower())

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

    def test_overlay_argtypes_accept_large_hwnd(self) -> None:
        import ctypes

        big = 0x00007FFA_12B3_C4D5
        mapping = overlay_api_argtypes()
        hwnd_t, hdc_t = mapping["GetDC"][0][0], mapping["GetDC"][1]
        hbm_t = mapping["CreateDIBSection"][1]
        select_obj = mapping["SelectObject"][0][1]
        setpos_hwnd = mapping["SetWindowPos"][0][0]
        setpos_insert = mapping["SetWindowPos"][0][1]
        self.assertIsNot(setpos_hwnd, ctypes.c_int)
        self.assertIsNot(setpos_insert, ctypes.c_int)
        self.assertIsNot(select_obj, ctypes.c_int)
        self.assertGreaterEqual(ctypes.sizeof(setpos_hwnd), ctypes.sizeof(ctypes.c_void_p))
        self.assertGreaterEqual(ctypes.sizeof(select_obj), ctypes.sizeof(ctypes.c_void_p))
        for typ in (hwnd_t, hdc_t, hbm_t, select_obj, setpos_hwnd, setpos_insert):
            coerce_win_handle(typ, big)
            typ(big)
        hwnd_topmost_handle()
        coerce_win_handle(hwnd_t, -1)

        class Fn:
            argtypes = None
            restype = None

        class Dll:
            pass

        dll = Dll()
        for name in overlay_api_argtypes():
            setattr(dll, name, Fn())
        apply_overlay_argtypes(user32=dll, gdi32=dll, kernel32=dll)
        dll.SelectObject.argtypes[1](big)
        dll.SetWindowPos.argtypes[0](big)
        dll.SetWindowPos.argtypes[1](-1)
        dll.CreateDIBSection.restype(big)


class OverlayMarshalTests(unittest.TestCase):
    def tearDown(self) -> None:
        from desk_pilot.desktop.overlay import close_overlay, set_overlay_pump

        set_overlay_pump(None)
        close_overlay()

    def test_no_pump_runs_inline(self) -> None:
        from desk_pilot.desktop.overlay import call_on_overlay_thread, overlay_owner_thread_id

        self.assertIsNone(overlay_owner_thread_id())
        me = threading.get_ident()
        self.assertEqual(call_on_overlay_thread(lambda: threading.get_ident()), me)

    def test_same_thread_skips_pump(self) -> None:
        from desk_pilot.desktop.overlay import call_on_overlay_thread, set_overlay_pump

        pumped: list[int] = []

        def pump(fn: Any) -> None:
            pumped.append(1)
            fn()

        set_overlay_pump(pump)
        self.assertEqual(call_on_overlay_thread(lambda: 7), 7)
        self.assertEqual(pumped, [])

    def test_worker_job_runs_on_owner_via_queue(self) -> None:
        import queue as queue_mod

        from desk_pilot.desktop.overlay import (
            call_on_overlay_thread,
            overlay_owner_thread_id,
            overlay_thread_note,
            set_overlay_pump,
        )

        jobs: queue_mod.Queue[Any] = queue_mod.Queue()
        owner = threading.get_ident()

        def pump(fn: Any) -> None:
            jobs.put(fn)

        set_overlay_pump(pump)
        self.assertEqual(overlay_owner_thread_id(), owner)
        seen: dict[str, int] = {}

        def worker() -> None:
            seen["tid"] = call_on_overlay_thread(lambda: threading.get_ident())

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.time() + 2
        while thread.is_alive() and time.time() < deadline:
            try:
                job = jobs.get(timeout=0.05)
            except queue_mod.Empty:
                continue
            self.assertEqual(threading.get_ident(), owner)
            job()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen.get("tid"), owner)
        self.assertIn("tid=", overlay_thread_note())
        self.assertIn("owner=", overlay_thread_note())

    def test_marshal_propagates_exception(self) -> None:
        import queue as queue_mod

        from desk_pilot.desktop.overlay import call_on_overlay_thread, set_overlay_pump

        jobs: queue_mod.Queue[Any] = queue_mod.Queue()
        set_overlay_pump(jobs.put)
        caught: list[BaseException] = []

        def worker() -> None:
            try:
                call_on_overlay_thread(_raise_value_error)
            except ValueError as exc:
                caught.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.time() + 2
        while thread.is_alive() and time.time() < deadline:
            try:
                jobs.get(timeout=0.05)()
            except queue_mod.Empty:
                continue
        thread.join(timeout=1)
        self.assertEqual(len(caught), 1)
        self.assertEqual(str(caught[0]), "overlay boom")

    def test_marshal_timeout(self) -> None:
        from desk_pilot.desktop.overlay import call_on_overlay_thread, set_overlay_pump

        set_overlay_pump(lambda _fn: None)
        caught: list[BaseException] = []

        def worker() -> None:
            try:
                call_on_overlay_thread(lambda: 1, timeout=0.2)
            except TimeoutError as exc:
                caught.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(len(caught), 1)
        self.assertIn("timed out", str(caught[0]))

    def test_wrong_thread_hwnd_is_dropped(self) -> None:
        overlay = HighlightOverlay()
        overlay._hwnd = 99
        overlay._hwnd_tid = threading.get_ident() + 999
        self.assertFalse(overlay._hwnd_usable())
        self.assertEqual(overlay._hwnd, 0)
        self.assertIsNone(overlay._hwnd_tid)


def _raise_value_error() -> None:
    raise ValueError("overlay boom")


_ERR_1400 = "UpdateLayeredWindow failed (GetLastError=1400 Invalid window handle.)"
_ERR_87 = "UpdateLayeredWindow failed (GetLastError=87 Invalid parameter.)"


class _StubOverlay(HighlightOverlay):
    """Win32-free overlay: create/blit/hide/destroy are recorded fakes."""

    def __init__(self, blit_script: list[tuple[bool, str | None]] | None = None) -> None:
        super().__init__()
        self.creates: list[int] = []
        self.destroys: list[int] = []
        self.paints: list[int] = []
        self.hides: list[int] = []
        self._blit_script = list(blit_script or [])
        self._next = 400

    def _create_hwnd(self) -> tuple[int, str | None]:
        self._next += 1
        self.creates.append(self._next)
        return self._next, None

    def _destroy_hwnd(self, hwnd: int) -> None:
        self.destroys.append(hwnd)

    def _hide_hwnd(self, hwnd: int) -> None:
        self.hides.append(hwnd)

    def _hwnd_is_alive(self, hwnd: int) -> bool:
        return bool(hwnd) and hwnd not in self.destroys

    def _paint_hwnd(self, hwnd: int, image: Any, origin_x: int, origin_y: int) -> tuple[bool, str | None]:
        self.paints.append(hwnd)
        if self._blit_script:
            return self._blit_script.pop(0)
        return True, None


def _dummy_sketch_image() -> Any:
    from PIL import Image

    return Image.new("RGBA", (16, 16), (255, 220, 0, 200))


def _drain_overlay_jobs(jobs: Any, thread: threading.Thread, timeout: float = 3.0) -> None:
    import queue as queue_mod

    deadline = time.time() + timeout
    while thread.is_alive() and time.time() < deadline:
        try:
            jobs.get(timeout=0.05)()
        except queue_mod.Empty:
            continue
    thread.join(timeout=1)


class OverlayHardenTests(unittest.TestCase):
    def tearDown(self) -> None:
        from desk_pilot.desktop.overlay import close_overlay, set_overlay_pump

        set_overlay_pump(None)
        close_overlay()

    def test_invalid_hwnd_error_detects_1400(self) -> None:
        from desk_pilot.desktop.overlay import ERROR_INVALID_WINDOW_HANDLE, is_invalid_hwnd_error

        self.assertEqual(ERROR_INVALID_WINDOW_HANDLE, 1400)
        self.assertTrue(is_invalid_hwnd_error(_ERR_1400))
        self.assertTrue(is_invalid_hwnd_error("x", code=1400))
        self.assertFalse(is_invalid_hwnd_error(_ERR_87))
        self.assertFalse(is_invalid_hwnd_error(None))

    def test_1400_recreates_hwnd_and_retries(self) -> None:
        overlay = _StubOverlay([(False, _ERR_1400), (True, None)])
        result = overlay._show_win32([120, 80, 520, 280], _dummy_sketch_image(), 120, 80)
        self.assertTrue(result["ok"])
        self.assertTrue(result["recreated"])
        self.assertEqual(overlay.creates, [401, 402])
        self.assertEqual(overlay.paints, [401, 402])
        self.assertEqual(overlay.destroys, [401])
        self.assertEqual(overlay._hwnd, 402)

    def test_1400_recreate_still_failing_clears_hwnd(self) -> None:
        overlay = _StubOverlay([(False, _ERR_1400), (False, _ERR_1400)])
        result = overlay._show_win32([120, 80, 520, 280], _dummy_sketch_image(), 120, 80)
        self.assertFalse(result["ok"])
        self.assertTrue(result["recreated"])
        self.assertIn("hwnd recreated, still failed", result["error"] or "")
        self.assertIn("GetLastError=1400", result["error"] or "")
        self.assertEqual(overlay._hwnd, 0)
        self.assertIsNone(overlay._hwnd_tid)
        self.assertEqual(overlay.destroys, [401, 402])

    def test_non_1400_fail_does_not_retry_and_drops_hwnd(self) -> None:
        overlay = _StubOverlay([(False, _ERR_87)])
        result = overlay._show_win32([120, 80, 520, 280], _dummy_sketch_image(), 120, 80)
        self.assertFalse(result["ok"])
        self.assertFalse(result["recreated"])
        self.assertIn("GetLastError=87", result["error"] or "")
        self.assertNotIn("hwnd recreated", result["error"] or "")
        self.assertEqual(len(overlay.creates), 1)
        self.assertEqual(len(overlay.paints), 1)
        self.assertEqual(overlay._hwnd, 0)
        self.assertEqual(overlay.destroys, [401])

    def test_worker_show_hide_stress_via_marshal(self) -> None:
        """N consecutive show/hide from a fake guide worker; no 1400, hwnd reused."""
        import queue as queue_mod

        from desk_pilot.desktop.overlay import call_on_overlay_thread, set_overlay_pump

        jobs: queue_mod.Queue[Any] = queue_mod.Queue()
        set_overlay_pump(jobs.put)
        overlay = _StubOverlay()
        image = _dummy_sketch_image()
        box = [120, 80, 520, 280]
        rounds = 25
        results: list[dict[str, Any]] = []

        def worker() -> None:
            for _ in range(rounds):
                results.append(
                    call_on_overlay_thread(lambda: overlay._show_win32(box, image, 120, 80))
                )
                call_on_overlay_thread(overlay._hide_win32)

        thread = threading.Thread(target=worker)
        thread.start()
        _drain_overlay_jobs(jobs, thread)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), rounds)
        self.assertTrue(all(item.get("ok") for item in results))
        self.assertFalse(any(item.get("recreated") for item in results))
        self.assertFalse(any("1400" in str(item.get("error") or "") for item in results))
        self.assertEqual(overlay.creates, [401], "HWND must be reused across show/hide")
        self.assertEqual(len(overlay.paints), rounds)
        self.assertEqual(len(overlay.hides), rounds)
        self.assertEqual(overlay.destroys, [])
        self.assertEqual(overlay._hwnd, 401)

    def test_worker_1400_retry_via_marshal_succeeds(self) -> None:
        import queue as queue_mod

        from desk_pilot.desktop.overlay import call_on_overlay_thread, set_overlay_pump

        jobs: queue_mod.Queue[Any] = queue_mod.Queue()
        set_overlay_pump(jobs.put)
        overlay = _StubOverlay([(False, _ERR_1400), (True, None)])
        seen: dict[str, Any] = {}

        def worker() -> None:
            seen["result"] = call_on_overlay_thread(
                lambda: overlay._show_win32([10, 10, 80, 40], _dummy_sketch_image(), 10, 10)
            )

        thread = threading.Thread(target=worker)
        thread.start()
        _drain_overlay_jobs(jobs, thread)
        self.assertFalse(thread.is_alive())
        result = seen["result"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["recreated"])
        self.assertEqual(overlay.creates, [401, 402])
        self.assertEqual(overlay._hwnd, 402)


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
        self.assertFalse(is_guide_goal("open tldraw and sketch me a car"))
        self.assertFalse(is_guide_goal("sketch me a car"))

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

    def test_recreated_hwnd_is_logged(self) -> None:
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
        original = desk.show_highlight

        def wrapped(rect: Any) -> dict[str, Any]:
            result = dict(original(rect))
            result["recreated"] = True
            return result

        desk.show_highlight = wrapped  # type: ignore[method-assign]
        AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=6,
            force_guide=True,
            await_step=lambda: "continue",
            on_log=lambda k, m: logs.append((k, m)),
        ).run("how to click Start")
        self.assertTrue(any(k == "sketch" and "hwnd recreated" in m for k, m in logs))
        self.assertTrue(any("[0, 1040, 48, 1080]" in m for k, m in logs if k == "sketch"))

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

    def test_zero_zero_guide_sketches_window_not_origin_pad(self) -> None:
        from desk_pilot.agent.guide import resolve_guide_rect

        desk = MockDesktop()
        resolved = resolve_guide_rect(desk, {"instruction": "Click", "x": 0, "y": 0})
        self.assertEqual(resolved["rect"], [0, 0, 1920, 1080])
        self.assertEqual(resolved["source"], "window")
        self.assertTrue(resolved["fallback"])
        self.assertNotEqual(resolved["rect"], [-12, -12, 12, 12])

        logs: list[tuple[str, str]] = []
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call(
                            "guide_step",
                            '{"instruction":"Switch to the already-open browser","x":0,"y":0}',
                            "1",
                        )
                    ],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"ok"}', "2")]},
            ]
        )
        AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=5,
            force_guide=True,
            await_step=lambda: "continue",
            on_log=lambda k, m: logs.append((k, m)),
        ).run("how do I toggle memory in chatgpt web")
        self.assertEqual(desk.highlights, [[0, 0, 1920, 1080]])
        self.assertTrue(any(k == "sketch" and "[0, 0, 1920, 1080]" in m for k, m in logs))
        self.assertFalse(any("[-12, -12, 12, 12]" in m for _, m in logs))
        self.assertFalse(any("OverflowError" in m for _, m in logs))

    def test_window_title_guide_uses_real_window_rect(self) -> None:
        from desk_pilot.agent.guide import resolve_guide_rect

        chatgpt = [96, 48, 1340, 880]
        desk = MockDesktop()
        desk._upsert_app("helium", "ChatGPT", "browser", rect=chatgpt)
        resolved = resolve_guide_rect(
            desk,
            {"instruction": "Switch to the already-open ChatGPT window", "name": "ChatGPT"},
        )
        self.assertEqual(resolved["rect"], chatgpt)
        self.assertEqual(resolved["source"], "window")
        titled = resolve_guide_rect(
            desk,
            {
                "instruction": "Switch to ChatGPT",
                "expected_title": "ChatGPT",
            },
        )
        self.assertEqual(titled["rect"], chatgpt)
        focus = resolve_guide_rect(
            desk,
            {"title_contains": "ChatGPT"},
            tool_name="focus_window",
        )
        self.assertEqual(focus["rect"], chatgpt)

        logs: list[tuple[str, str]] = []
        llm = ScriptedLLM(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call(
                            "guide_step",
                            '{"instruction":"Switch to the already-open ChatGPT window","name":"ChatGPT"}',
                            "1",
                        )
                    ],
                },
                {"content": "", "tool_calls": [_call("done", '{"result":"ok"}', "2")]},
            ]
        )
        AgentLoop(
            backend=desk,
            llm=llm,
            max_steps=5,
            force_guide=True,
            await_step=lambda: "continue",
            on_log=lambda k, m: logs.append((k, m)),
        ).run("how do I toggle memory in chatgpt web")
        self.assertEqual(desk.highlights, [chatgpt])
        self.assertTrue(any(k == "sketch" and "[96, 48, 1340, 880]" in m for k, m in logs))
        self.assertFalse(any("[-12, -12, 12, 12]" in m for _, m in logs))
        self.assertFalse(any(m.startswith("failed:") for k, m in logs if k == "sketch"))

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
