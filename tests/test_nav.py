import unittest

from desk_pilot.agent.nav import (
    address_control_from_snapshot,
    is_url_like,
    looks_like_navigation,
    nav_changed_toward,
    nav_fingerprint,
    run_navigate,
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


class NavigateToolTests(unittest.TestCase):
    def test_navigate_success(self) -> None:
        desk = MockDesktop()
        desk._open_tldraw()
        result = run_navigate(desk, "https://images.google.com")
        self.assertTrue(result["nav_ok"])
        self.assertFalse(result["nav_failed"])
        self.assertTrue(result["ok"])
        self.assertIn("google", (result.get("title") or desk.window_title).lower())
        self.assertIn("google", desk.window_title.lower())
        self.assertTrue(any(a.startswith("type ") for a in desk.actions))
        self.assertTrue(any(a == "hotkey enter" for a in desk.actions))

    def test_navigate_stale_title_after_enter_is_failed(self) -> None:
        desk = MockDesktop()
        desk._open_tldraw()
        desk.window_title = "brawlhalla.exe - Helium"
        desk.stale_nav = True
        result = run_navigate(desk, "https://www.google.com/imghp")
        self.assertTrue(result["nav_failed"])
        self.assertFalse(result["nav_ok"])
        self.assertFalse(result["ok"])
        self.assertEqual(desk.window_title, "brawlhalla.exe - Helium")
        self.assertIn("stale", (result.get("reason") or "").lower())
        self.assertIn("brawlhalla.exe - Helium", result.get("title") or desk.window_title)

    def test_navigate_missing_address_control(self) -> None:
        desk = MockDesktop()
        desk._open_tldraw()
        desk.hide_address_bar = True
        tree = desk.list_ui()
        self.assertIsNone(address_control_from_snapshot(tree))
        result = run_navigate(desk, "https://example.com")
        self.assertTrue(result["nav_failed"])
        self.assertFalse(result["nav_ok"])
        self.assertIn("missing address control", (result.get("reason") or "").lower())
        self.assertFalse(any(a.startswith("type ") for a in desk.actions))

    def test_navigate_rejects_non_chromium(self) -> None:
        desk = MockDesktop()
        desk._open_notepad()
        result = run_navigate(desk, "https://example.com")
        self.assertTrue(result["nav_failed"])
        self.assertIn("Chromium-family", result.get("reason") or "")


if __name__ == "__main__":
    unittest.main()
