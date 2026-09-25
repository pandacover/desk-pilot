"""Geometry helpers for highlight rects. No Pillow — safe to import from the agent loop."""

from __future__ import annotations

from typing import Any

SKIP_NO_RECT = "no rect (need name/automation_id/xy)"
TEST_SKETCH_RECT = [120, 80, 520, 280]
TEST_SKETCH_SECONDS = 2.0


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
