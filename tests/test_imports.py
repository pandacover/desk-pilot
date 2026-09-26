import unittest


class ImportSmokeTests(unittest.TestCase):
    def test_package_exports(self) -> None:
        import desk_pilot
        from desk_pilot.desktop import get_backend

        self.assertEqual(desk_pilot.DEFAULT_MODEL, "openai/gpt-6-luna")
        self.assertEqual(desk_pilot.__version__, "0.2.9")
        backend = get_backend(force_mock=True)
        self.assertTrue(backend.dry_run)
        tree = backend.list_ui()
        self.assertIn("controls", tree)

    def test_ui_module_imports(self) -> None:
        try:
            from desk_pilot.app import ui
        except ModuleNotFoundError as exc:
            if "tkinter" in str(exc):
                self.skipTest("tkinter (python3-tk) is not installed in this environment")
            raise

        self.assertTrue(hasattr(ui, "DeskPilotApp"))
        self.assertTrue(hasattr(ui, "run_app"))

    def test_launch_backend_method(self) -> None:
        from desk_pilot.desktop import get_backend

        backend = get_backend(force_mock=True)
        self.assertTrue(backend.launch_app("notepad")["ok"])
        self.assertTrue(backend.launch_app("notepad").get("reused"))
        self.assertFalse(backend.launch_app("helium")["ok"])

    def test_overlay_modules_import(self) -> None:
        from desk_pilot.desktop import overlay, sketch

        self.assertTrue(callable(sketch.render_sketch))
        self.assertTrue(callable(overlay.get_overlay))
        self.assertTrue(callable(overlay.set_overlay_pump))
        self.assertTrue(callable(overlay.call_on_overlay_thread))
        self.assertTrue(callable(overlay.is_invalid_hwnd_error))
        self.assertEqual(overlay.ERROR_INVALID_WINDOW_HANDLE, 1400)
        from desk_pilot.agent.guide import is_guide_goal

        self.assertFalse(is_guide_goal("open notepad"))


if __name__ == "__main__":
    unittest.main()
