"""Geometry helpers for highlight rects. No Pillow — safe to import from the agent loop."""

from __future__ import annotations

from typing import Any

SKIP_NO_RECT = "no rect (need name/automation_id/xy)"
SKIP_ORIGIN_PAD = "placeholder origin rect (0,0 is not a control)"
SKIP_ABSURD_RECT = "rect coordinates out of range"
TEST_SKETCH_RECT = [120, 80, 520, 280]
TEST_SKETCH_SECONDS = 2.0
MAX_ABS_COORD = 100_000
MAX_EDGE = 16_384


def as_rect(rect: Any) -> list[int] | None:
    """Coerce a list or tuple of four numbers into [left, top, right, bottom]."""
    if rect is None:
        return None
    if not isinstance(rect, (list, tuple)) or len(rect) < 4:
        return None
    try:
        left, top, right, bottom = (int(round(float(v))) for v in rect[:4])
    except (TypeError, ValueError):
        return None
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    if (right - left) < 1 and (bottom - top) < 1:
        return None
    return [left, top, right, bottom]


def usable_screen_point(x: Any, y: Any) -> bool:
    """True when x,y can stand in for a control. (0,0) is the empty/default click, not a target."""
    if x is None or y is None or x == "" or y == "":
        return False
    try:
        xi, yi = int(x), int(y)
    except (TypeError, ValueError):
        return False
    if xi == 0 and yi == 0:
        return False
    if abs(xi) > MAX_ABS_COORD or abs(yi) > MAX_ABS_COORD:
        return False
    return True


def is_placeholder_origin_rect(rect: Any) -> bool:
    """The ±10/±12 pad around (0,0) that find_control_rect used when x=y=0."""
    box = as_rect(rect)
    if not box:
        return False
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    return abs(cx) <= 12 and abs(cy) <= 12 and width <= 28 and height <= 28


def coords_in_range(rect: Any) -> bool:
    box = as_rect(rect)
    if not box:
        return False
    left, top, right, bottom = box
    if any(abs(v) > MAX_ABS_COORD for v in box):
        return False
    if (right - left) > MAX_EDGE or (bottom - top) > MAX_EDGE:
        return False
    return True


def rect_skip_reason(rect: Any) -> str | None:
    box = as_rect(rect)
    if not box:
        return SKIP_NO_RECT
    if is_placeholder_origin_rect(box):
        return SKIP_ORIGIN_PAD
    if not coords_in_range(box):
        return SKIP_ABSURD_RECT
    return None


def sketchable_rect(rect: Any) -> list[int] | None:
    """Rect that is safe to blit: not empty, not the (0,0) pad, not absurd."""
    if rect_skip_reason(rect):
        return None
    return as_rect(rect)


def xy_pad_rect(x: Any, y: Any, pad: int = 12) -> list[int] | None:
    """Tiny box around a real screen point. (0,0) and out-of-range points are rejected."""
    if not usable_screen_point(x, y):
        return None
    xi, yi = int(x), int(y)
    return sketchable_rect([xi - pad, yi - pad, xi + pad, yi + pad])
