import unittest

from desk_pilot.desktop.mock import MockDesktop


class MockDesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.desk = MockDesktop()

    def test_list_ui_starts_on_desktop(self) -> None:
        tree = self.desk.list_ui()
        self.assertTrue(tree["dry_run"])
        self.assertEqual(tree["window"]["name"], "Desktop")
        names = [c["name"] for c in tree["controls"]]
        self.assertIn("Start", names)
        for item in tree["controls"]:
            self.assertIn("name", item)
            self.assertIn("type", item)
            self.assertIn("automation_id", item)
            self.assertIn("rect", item)
            self.assertIn("path", item)

    def test_open_notepad_and_type_hello(self) -> None:
        self.assertTrue(self.desk.hotkey("win+r")["ok"])
        self.assertEqual(self.desk.list_ui()["window"]["name"], "Run")
        self.desk.type_text("notepad")
        self.desk.hotkey("enter")
        tree = self.desk.list_ui()
        self.assertIn("Notepad", tree["window"]["name"])
        typed = self.desk.type_text("hello")
        self.assertTrue(typed["ok"])
        self.assertEqual(typed["value"], "hello")
        self.assertTrue(self.desk.wait_for_window("Notepad")["ok"])

    def test_click_start(self) -> None:
        result = self.desk.click(name="Start")
        self.assertTrue(result["ok"])
        self.assertEqual(self.desk.list_ui()["window"]["name"], "Start")

    def test_missing_control(self) -> None:
        result = self.desk.click(name="No Such Button")
        self.assertFalse(result["ok"])

    def test_screenshot_writes_png(self) -> None:
        result = self.desk.screenshot_region(0, 0, 80, 40)
        self.assertTrue(result["ok"])
        from pathlib import Path

        self.assertTrue(Path(result["path"]).is_file())

    def test_win_r_unknown_app_is_detectable(self) -> None:
        self.desk.hotkey("win+r")
        self.desk.type_text("helium")
        entered = self.desk.hotkey("enter")
        self.assertFalse(entered["ok"])
        tree = self.desk.list_ui()
        self.assertTrue(tree.get("launch_error"))
        self.assertIn("cannot find", (tree.get("error") or "").lower())
        waited = self.desk.wait_for_window("Helium", timeout_seconds=0.2)
        self.assertFalse(waited["ok"])
        self.assertTrue(waited.get("launch_error"))

    def test_launch_app_opens_notepad(self) -> None:
        result = self.desk.launch_app("Notepad")
        self.assertTrue(result["ok"])
        self.assertIn("Notepad", self.desk.list_ui()["window"]["name"])

    def test_dismiss_run_error_then_launch(self) -> None:
        self.desk.hotkey("win+r")
        self.desk.type_text("helium")
        self.desk.hotkey("enter")
        self.desk.click(name="OK")
        self.assertEqual(self.desk.list_ui()["window"]["name"], "Desktop")
        self.assertTrue(self.desk.launch_app("notepad")["ok"])

    def test_launch_app_reuses_open_notepad(self) -> None:
        self.desk.launch_app("Notepad")
        self.desk.type_text("hello")
        self.desk.scene = "desktop"
        self.desk.window_title = "Desktop"
        titles = [w["name"] for w in self.desk.list_windows()["windows"]]
        self.assertTrue(any("Notepad" in t for t in titles))
        again = self.desk.launch_app("Notepad")
        self.assertTrue(again["ok"])
        self.assertTrue(again.get("reused"))
        self.assertEqual(again.get("method"), "existing_window")
        self.assertIn("Notepad", self.desk.window_title)
        self.assertEqual(self.desk.edit_text, "hello")
        focused = self.desk.focus_window(title_contains="Notepad")
        self.assertTrue(focused["ok"])


if __name__ == "__main__":
    unittest.main()
