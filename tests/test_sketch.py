import unittest

from desk_pilot.desktop.mock import MockDesktop
from desk_pilot.desktop.overlay import HighlightOverlay, get_overlay
from desk_pilot.desktop.sketch import (
    distance_to_rect_border,
    expand_tiny_rect,
    normalize_rect,
    render_sketch,
    sketch_polyline,
)


class SketchPathTests(unittest.TestCase):
    def test_points_stay_near_rect(self) -> None:
        left, top, right, bottom = 120, 80, 360, 220
        path = sketch_polyline(left, top, right, bottom, amplitude=5, overshoot=3, seed=7)
        self.assertGreaterEqual(len(path), 20)
        self.assertEqual(path[0], path[-1])
        for x, y in path:
            dist = distance_to_rect_border(x, y, left, top, right, bottom)
            self.assertLessEqual(dist, 14)

    def test_double_stroke_differs(self) -> None:
        a = sketch_polyline(10, 10, 80, 50, seed=1, stroke=0)
        b = sketch_polyline(10, 10, 80, 50, seed=1, stroke=1)
        self.assertNotEqual(a, b)

    def test_normalize_swaps_inverted(self) -> None:
        self.assertEqual(normalize_rect([40, 30, 10, 5]), (10, 5, 40, 30))
        self.assertEqual(normalize_rect(None), (0, 0, 0, 0))

    def test_expand_tiny(self) -> None:
        left, top, right, bottom = expand_tiny_rect(50, 50, 52, 51, min_size=12)
        self.assertGreaterEqual(right - left, 12)
        self.assertGreaterEqual(bottom - top, 12)

    def test_render_is_rgba_with_ink(self) -> None:
        image, origin_x, origin_y = render_sketch([200, 100, 340, 180], seed=3)
        self.assertEqual(image.mode, "RGBA")
        self.assertLess(origin_x, 200)
        self.assertLess(origin_y, 100)
        alpha_max = image.getextrema()[3][1]
        self.assertGreater(alpha_max, 0)


class OverlayToggleTests(unittest.TestCase):
    def test_disabled_does_not_record(self) -> None:
        desk = MockDesktop()
        desk.highlight_overlay = False
        desk.click(name="Start")
        desk.type_text("hello")
        desk.click(x=10, y=20)
        self.assertEqual(desk.highlights, [])

    def test_enabled_records_control_rect(self) -> None:
        desk = MockDesktop()
        self.assertTrue(desk.highlight_overlay)
        desk.click(name="Start")
        self.assertEqual(len(desk.highlights), 1)
        left, top, right, bottom = desk.highlights[0]
        self.assertLess(left, right)
        self.assertLess(top, bottom)

    def test_linux_overlay_flash_is_noop(self) -> None:
        overlay = HighlightOverlay()
        overlay.flash([10, 10, 80, 40], duration=1.0)
        overlay.hide()
        overlay.close()
        get_overlay().flash(None)


if __name__ == "__main__":
    unittest.main()
