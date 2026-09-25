import unittest

from desk_pilot.__main__ import main


class CliTests(unittest.TestCase):
    def test_cli_without_goal_exits_2(self) -> None:
        self.assertEqual(main(["--cli"]), 2)

    def test_cli_without_key_exits_2(self) -> None:
        self.assertEqual(main(["--cli", "--goal", "Open Notepad and type hello"]), 2)

    def test_help(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
