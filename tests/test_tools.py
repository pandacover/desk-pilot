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
                "type_text",
                "hotkey",
                "screenshot_region",
                "wait_for_window",
                "done",
                "fail",
            },
        )

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


if __name__ == "__main__":
    unittest.main()
