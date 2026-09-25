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


if __name__ == "__main__":
    unittest.main()
