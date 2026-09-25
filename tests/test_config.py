import json
import os
import tempfile
import unittest
from pathlib import Path

from desk_pilot.app.config import Settings, load_settings, save_settings


class ConfigTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = Settings(
                openrouter_api_key="sk-or-v1-test-secret",
                model="openai/gpt-6-luna",
                max_steps=12,
                reasoning_effort="low",
                guide_mode=True,
            )
            save_settings(original, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.openrouter_api_key, "sk-or-v1-test-secret")
            self.assertEqual(loaded.model, "openai/gpt-6-luna")
            self.assertEqual(loaded.max_steps, 12)
            self.assertTrue(loaded.guide_mode)
            raw = path.read_text(encoding="utf-8")
            self.assertIn("openrouter_api_key", json.loads(raw))

    def test_env_overrides_saved_key(self) -> None:
        settings = Settings(openrouter_api_key="saved-key")
        old = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "env-key"
        try:
            self.assertEqual(settings.effective_api_key(), "env-key")
            self.assertTrue(settings.key_from_env())
        finally:
            if old is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = old

    def test_masked_key_hides_secret(self) -> None:
        settings = Settings(openrouter_api_key="sk-or-v1-abcdefghijklmnop")
        masked = settings.masked_key()
        self.assertNotIn("abcdefgh", masked)
        self.assertTrue(masked.endswith("mnop") or "…" in masked)

    def test_corrupt_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            loaded = load_settings(path)
            self.assertEqual(loaded.openrouter_api_key, "")
            self.assertEqual(loaded.model, "openai/gpt-6-luna")
            self.assertFalse(loaded.guide_mode)

    def test_missing_guide_mode_defaults_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"openrouter_api_key":"","model":"openai/gpt-6-luna","max_steps":30}\n', encoding="utf-8")
            loaded = load_settings(path)
            self.assertFalse(loaded.guide_mode)


if __name__ == "__main__":
    unittest.main()
