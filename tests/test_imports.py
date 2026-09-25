import unittest


class ImportSmokeTests(unittest.TestCase):
    def test_package_exports(self) -> None:
        import desk_pilot
        from desk_pilot.desktop import get_backend

        self.assertEqual(desk_pilot.DEFAULT_MODEL, "openai/gpt-6-luna")
        backend = get_backend(force_mock=True)
        self.assertTrue(backend.dry_run)
        tree = backend.list_ui()
        self.assertIn("controls", tree)

    def test_ui_module_imports(self) -> None:
        from desk_pilot.app import ui

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


if __name__ == "__main__":
    unittest.main()
