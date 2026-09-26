import unittest

from desk_pilot.agent.tools import TOOL_DEFINITIONS, TerminalCall, dispatch_tool, parse_arguments
from desk_pilot.desktop.mock import MockDesktop


class ToolDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.desk = MockDesktop()

    def test_tool_names(self) -> None:
        names = {item["function"]["name"] for item in TOOL_DEFINITIONS}
        self.assertEqual(
            names,
            {
                "list_ui",
                "click",
                "drag",
                "prepare_art",
                "type_text",
                "hotkey",
                "screenshot_region",
                "list_windows",
                "focus_window",
                "launch_app",
                "wait_for_window",
                "find_files",
                "verify_file",
                "guide_step",
                "navigate",
                "done",
                "fail",
            },
        )

    def test_guide_tool_subset(self) -> None:
        from desk_pilot.agent.tools import GUIDE_TOOL_DEFINITIONS

        names = {item["function"]["name"] for item in GUIDE_TOOL_DEFINITIONS}
        self.assertEqual(names, {"list_ui", "list_windows", "guide_step", "done", "fail"})
        self.assertNotIn("click", names)
        self.assertNotIn("drag", names)
        self.assertNotIn("prepare_art", names)

    def test_dispatch_list_and_hotkey(self) -> None:
        tree = dispatch_tool(self.desk, "list_ui", {})
        self.assertIn("controls", tree)
        result = dispatch_tool(self.desk, "hotkey", {"keys": "win+r"})
        self.assertTrue(result["ok"])

    def test_done_and_fail_are_terminal(self) -> None:
        done = dispatch_tool(self.desk, "done", {"result": "typed hello"})
        self.assertIsInstance(done, TerminalCall)
        self.assertEqual(done.name, "done")
        fail = dispatch_tool(self.desk, "fail", {"reason": "blocked"})
        self.assertIsInstance(fail, TerminalCall)
        self.assertEqual(fail.name, "fail")

    def test_unknown_tool(self) -> None:
        result = dispatch_tool(self.desk, "explode", {})
        self.assertFalse(result["ok"])

    def test_parse_arguments(self) -> None:
        self.assertEqual(parse_arguments('{"keys":"ctrl+s"}'), {"keys": "ctrl+s"})
        self.assertEqual(parse_arguments({}), {})
        self.assertIn("_raw", parse_arguments("not-json"))

    def test_launch_app_notepad(self) -> None:
        result = dispatch_tool(self.desk, "launch_app", {"name": "notepad"})
        self.assertTrue(result["ok"])
        self.assertIn("Notepad", self.desk.list_ui()["window"]["name"])

    def test_launch_app_missing_name(self) -> None:
        result = dispatch_tool(self.desk, "launch_app", {})
        self.assertFalse(result["ok"])

    def test_focus_window_dispatch(self) -> None:
        dispatch_tool(self.desk, "launch_app", {"name": "notepad"})
        listed = dispatch_tool(self.desk, "list_windows", {})
        names = [w["name"] for w in listed["windows"]]
        self.assertTrue(any("Notepad" in n for n in names))
        self.desk.scene = "desktop"
        self.desk.window_title = "Desktop"
        focused = dispatch_tool(self.desk, "focus_window", {"title_contains": "Notepad"})
        self.assertTrue(focused["ok"])
        self.assertIn("Notepad", self.desk.window_title)

    def test_guide_step_instruction_only_is_rejected(self) -> None:
        result = dispatch_tool(self.desk, "guide_step", {"instruction": "Click Start"})
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("skip"))
        self.assertIsNone(result.get("rect"))

    def test_guide_step_name_resolves_rect(self) -> None:
        result = dispatch_tool(
            self.desk, "guide_step", {"instruction": "Click Start", "name": "Start"}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("rect"), [0, 1040, 48, 1080])
        self.assertFalse(result.get("fallback"))

    def test_guide_step_unknown_name_falls_back_to_window(self) -> None:
        result = dispatch_tool(
            self.desk,
            "guide_step",
            {"instruction": "Click it", "name": "NoSuchControl"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("rect"), [0, 0, 1920, 1080])
        self.assertTrue(result.get("fallback"))
        self.assertIn("whole window", result.get("instruction") or "")

    def test_guide_step_window_title_uses_open_window_rect(self) -> None:
        self.desk._upsert_app("helium", "ChatGPT", "browser", rect=[96, 48, 1340, 880])
        result = dispatch_tool(
            self.desk,
            "guide_step",
            {"instruction": "Switch to ChatGPT", "name": "ChatGPT"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("rect"), [96, 48, 1340, 880])
        self.assertEqual(result.get("source"), "window")
        self.assertTrue(result.get("fallback"))

    def test_guide_step_zero_zero_does_not_sketch_origin_pad(self) -> None:
        result = dispatch_tool(
            self.desk,
            "guide_step",
            {"instruction": "Switch to the browser", "x": 0, "y": 0},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("rect"), [0, 0, 1920, 1080])
        self.assertNotEqual(result.get("rect"), [-12, -12, 12, 12])
        self.assertTrue(result.get("fallback"))

    def test_dispatch_drag(self) -> None:
        result = dispatch_tool(
            self.desk, "drag", {"x1": 100, "y1": 200, "x2": 300, "y2": 220}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.desk.drags[0]["from"], [100, 200])
        self.assertEqual(self.desk.drags[0]["to"], [300, 220])

    def test_dispatch_drag_missing_coords(self) -> None:
        result = dispatch_tool(self.desk, "drag", {"x1": 1})
        self.assertFalse(result["ok"])

    def test_dispatch_prepare_art_geometric(self) -> None:
        from pathlib import Path

        result = dispatch_tool(self.desk, "prepare_art", {"subject": "car"})
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("method"), "geometric")
        self.assertTrue(Path(result["path"]).is_file())
        self.assertFalse(result.get("clipboard"))
        self.assertTrue(result.get("playbook"))
        self.assertEqual(self.desk.clipboard_image, result["path"])

    def test_dispatch_find_files_brawlhalla(self) -> None:
        result = dispatch_tool(self.desk, "find_files", {"name": "brawlhalla.exe"})
        self.assertTrue(result["ok"])
        paths = [item["path"].lower() for item in result["results"]]
        self.assertTrue(any(p.endswith("brawlhalla.exe") for p in paths))
        self.assertFalse(any("edge" in p for p in paths))

    def test_dispatch_find_files_requires_name(self) -> None:
        result = dispatch_tool(self.desk, "find_files", {})
        self.assertFalse(result["ok"])

    def test_dispatch_verify_file_html_as_jpg(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cat.jpg"
            path.write_bytes(b"<!DOCTYPE html><html><body>search results</body></html>")
            result = dispatch_tool(self.desk, "verify_file", {"path": str(path), "expect": "image"})
            self.assertFalse(result["ok"])
            self.assertIn("html", (result.get("error") or "").lower())

    def test_dispatch_navigate_success(self) -> None:
        self.desk._open_tldraw()
        result = dispatch_tool(self.desk, "navigate", {"url": "https://images.google.com"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["nav_ok"])
        self.assertIn("google", self.desk.window_title.lower())

    def test_dispatch_navigate_requires_url(self) -> None:
        result = dispatch_tool(self.desk, "navigate", {})
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("nav_failed"))

    def test_dispatch_click_right_button(self) -> None:
        from desk_pilot.desktop.base import normalize_click_button

        self.assertEqual(normalize_click_button("right"), "right")
        self.assertEqual(normalize_click_button("double"), "double")
        self.assertEqual(normalize_click_button(None), "left")
        self.desk.launch_app("tldraw")
        result = dispatch_tool(self.desk, "click", {"x": 400, "y": 280, "button": "right"})
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("button"), "right")
        names = [c["name"] for c in self.desk.list_ui()["controls"]]
        self.assertIn("Save image as", names)


if __name__ == "__main__":
    unittest.main()
