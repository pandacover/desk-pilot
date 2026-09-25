import unittest

from desk_pilot.agent.nav import (
    is_url_like,
    looks_like_navigation,
    nav_changed_toward,
    nav_fingerprint,
    target_tokens,
    wait_for_navigation,
)
from desk_pilot.desktop.mock import MockDesktop


class NavHelperTests(unittest.TestCase):
    def test_url_like(self) -> None:
        self.assertTrue(is_url_like("https://www.google.com/imghp"))
        self.assertTrue(is_url_like("images.google.com"))
        self.assertFalse(is_url_like("brawlhalla.exe"))
        self.assertFalse(is_url_like("find notepad"))

    def test_tokens_from_google_images(self) -> None:
        tokens = target_tokens("https://www.google.com/imghp")
        self.assertIn("google", tokens)

    def test_stale_title_is_nav_failed(self) -> None:
        before = {"title": "brawlhalla.exe - Helium", "address": "https://www.google.com/imghp", "focused": "Address"}
        after = dict(before)
        ok, reason = nav_changed_toward(before, after, "https://www.google.com/imghp")
        self.assertFalse(ok)
        self.assertIn("stale", reason.lower())

    def test_title_change_toward_target_is_ok(self) -> None:
        before = {"title": "brawlhalla.exe - Helium", "address": "", "focused": "Address"}
        after = {"title": "Google Images - Helium", "address": "https://www.google.com/imghp", "focused": "Address"}
        ok, reason = nav_changed_toward(before, after, "https://www.google.com/imghp")
        self.assertTrue(ok)
        self.assertIn("nav_ok", reason)

    def test_address_bar_focus_counts_as_navigation(self) -> None:
        snap = {
            "focused": {"name": "Address and search bar", "automation_id": "urlbar", "type": "Edit"},
            "window": {"name": "Helium"},
        }
        self.assertTrue(looks_like_navigation("dank memes", snap))
        self.assertFalse(looks_like_navigation("dank memes", {"focused": {"name": "SearchBox"}}))

    def test_wait_detects_mock_stale_nav(self) -> None:
        desk = MockDesktop()
        desk._open_tldraw()
        desk.window_title = "brawlhalla.exe - Helium"
        desk.stale_nav = True
        desk.type_text("https://www.google.com/imghp", clear=True)
        before = nav_fingerprint(desk.list_ui())
        desk.hotkey("enter")
        verdict = wait_for_navigation(desk, "https://www.google.com/imghp", before, timeout_seconds=0.2)
        self.assertTrue(verdict["nav_failed"])
        self.assertEqual(desk.window_title, "brawlhalla.exe - Helium")

    def test_wait_detects_successful_nav(self) -> None:
        desk = MockDesktop()
        desk._open_tldraw()
        desk.type_text("https://images.google.com", clear=True)
        before = nav_fingerprint(desk.list_ui())
        desk.hotkey("enter")
        verdict = wait_for_navigation(desk, "https://images.google.com", before, timeout_seconds=0.2)
        self.assertTrue(verdict["nav_ok"])
        self.assertIn("google", desk.window_title.lower())


if __name__ == "__main__":
    unittest.main()
