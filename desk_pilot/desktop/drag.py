"""Mouse-drag path helpers (straight stroke or polyline)."""

from __future__ import annotations

from typing import Any, Sequence


def build_drag_path(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    points: Sequence[Any] | None = None,
    *,
    steps: int = 12,
) -> list[tuple[int, int]]:
    """Screen points for mouse-down → move → up. Optional polyline `points`."""
    poly = _coerce_points(points)
    if len(poly) >= 2:
        path: list[tuple[int, int]] = [poly[0]]
        for i in range(1, len(poly)):
            chunk = _line(poly[i - 1][0], poly[i - 1][1], poly[i][0], poly[i][1], max(2, min(6, steps // 2)))
            path.extend(chunk[1:])
        return path
    return _line(int(x1), int(y1), int(x2), int(y2), max(2, min(int(steps), 40)))


def _coerce_points(points: Sequence[Any] | None) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for item in points or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                out.append((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
        elif isinstance(item, dict):
            try:
                out.append((int(item["x"]), int(item["y"])))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _line(x1: int, y1: int, x2: int, y2: int, steps: int) -> list[tuple[int, int]]:
    if x1 == x2 and y1 == y2:
        return [(x1, y1), (x2, y2)]
    n = max(2, steps)
    pts: list[tuple[int, int]] = []
    for i in range(n + 1):
        t = i / n
        pts.append((int(round(x1 + (x2 - x1) * t)), int(round(y1 + (y2 - y1) * t))))
    return pts
