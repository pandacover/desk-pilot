import unittest

from desk_pilot.desktop.com import bind_uia_to_this_thread, com_thread, ensure_com, looks_like_com_error, uninitialize_com
from desk_pilot.desktop.launch import detect_run_not_found, score_name, which_candidates


class ComHelperTests(unittest.TestCase):
    def test_ensure_com_is_noop_off_windows(self) -> None:
        result = ensure_com()
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("skipped"))
        uninitialize_com()
        with com_thread():
            self.assertTrue(ensure_com()["ok"])

    def test_looks_like_com_error(self) -> None:
        self.assertTrue(looks_like_com_error("CoInitialize has not been called."))
        self.assertTrue(looks_like_com_error("HRESULT 0x800401F0"))
        self.assertFalse(looks_like_com_error("control not found"))

    def test_bind_uia_without_module_attrs(self) -> None:
        class Dummy:
            pass

        result = bind_uia_to_this_thread(Dummy())
        self.assertTrue(result["ok"])


class LaunchHelperTests(unittest.TestCase):
    def test_score_name(self) -> None:
        self.assertEqual(score_name("Helium", "Helium"), 100)
        self.assertGreater(score_name("helium", "Helium Browser"), 50)
        self.assertEqual(score_name("zzz", "Notepad"), 0)

    def test_detect_run_not_found(self) -> None:
        window = {"name": "helium", "class": "#32770"}
        controls = [
            {"name": "Windows cannot find 'helium'. Make sure you typed the name correctly, and then try again."}
        ]
        text = detect_run_not_found(window, controls)
        self.assertIsNotNone(text)
        self.assertIn("cannot find", text.lower())
        self.assertIsNone(detect_run_not_found({"name": "Untitled - Notepad"}, [{"name": "Edit"}]))

    def test_which_candidates_existing_file(self) -> None:
        import sys
        from pathlib import Path

        path = Path(sys.executable)
        found = which_candidates(str(path))
        self.assertTrue(any(Path(item).name == path.name for item in found))


if __name__ == "__main__":
    unittest.main()
