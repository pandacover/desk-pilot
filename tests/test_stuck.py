import unittest

from desk_pilot.agent.stuck import STUCK_MESSAGE, StuckTracker, canonical_args, snapshot_fingerprint


class StuckTrackerTests(unittest.TestCase):
    def test_three_unchanged_snapshots_force_change(self) -> None:
        snap = {
            "window": {"name": "File Explorer", "class": "CabinetWClass"},
            "focused": {"name": "Address", "type": "Edit", "automation_id": "1001"},
            "controls": [{"name": "Address", "type": "Edit", "automation_id": "1001"}],
        }
        tracker = StuckTracker()
        tracker.last_fp = snapshot_fingerprint(snap)
        event = {"name": "type_text", "args": {"text": "brawlhalla.exe"}, "ok": True}
        self.assertFalse(tracker.note([event], snap))
        self.assertFalse(tracker.note([event], snap))
        self.assertTrue(tracker.note([event], snap))
        self.assertIn("find_files", tracker.message())
        self.assertIn("STUCK", STUCK_MESSAGE)

    def test_same_failing_tool_trips_breaker(self) -> None:
        snap_a = {
            "window": {"name": "Desktop"},
            "focused": {"name": "Desktop", "type": "Pane"},
            "controls": [{"name": "Start", "type": "Button"}],
        }
        snap_b = {
            "window": {"name": "Desktop"},
            "focused": {"name": "Search", "type": "Edit"},
            "controls": [{"name": "Start", "type": "Button"}],
        }
        tracker = StuckTracker()
        tracker.last_sig = canonical_args("click", {"name": "Search"})
        fail = {"name": "click", "args": {"name": "Search"}, "ok": False}
        tracker.note([fail], snap_a)
        tracker.note([fail], snap_b)
        self.assertTrue(tracker.note([fail], snap_a))

    def test_canonical_args_rounds_coords(self) -> None:
        a = canonical_args("click", {"x": 101, "y": 202})
        b = canonical_args("click", {"x": 104, "y": 198})
        self.assertEqual(a, b)

    def test_changing_ui_resets(self) -> None:
        tracker = StuckTracker()
        first = {
            "window": {"name": "Desktop"},
            "focused": {"name": "Desktop"},
            "controls": [],
        }
        second = {
            "window": {"name": "Notepad"},
            "focused": {"name": "Text Editor"},
            "controls": [{"name": "File", "type": "MenuItem"}],
        }
        tracker.last_fp = snapshot_fingerprint(first)
        tracker.note([{"name": "hotkey", "args": {"keys": "win+r"}, "ok": True}], first)
        self.assertFalse(tracker.note([{"name": "type_text", "args": {"text": "notepad"}, "ok": True}], second))
        self.assertEqual(tracker.streak, 0)


if __name__ == "__main__":
    unittest.main()
