"""Hand-drawn sketch outline around a UIA bounding rect (pure geometry + Pillow)."""

from __future__ import annotations

import math
import random
from typing import Sequence

from PIL import Image, ImageDraw

from desk_pilot.desktop.rects import SKIP_NO_RECT, TEST_SKETCH_RECT, TEST_SKETCH_SECONDS, as_rect

PAD = 18
MIN_SIZE = 12
DEFAULT_AMPLITUDE = 4.5

__all__ = [
    "PAD",
    "MIN_SIZE",
    "DEFAULT_AMPLITUDE",
    "SKIP_NO_RECT",
    "TEST_SKETCH_RECT",
    "TEST_SKETCH_SECONDS",
    "as_rect",
    "normalize_rect",
    "expand_tiny_rect",
    "sketch_polyline",
    "render_sketch",
    "distance_to_rect_border",
]


def normalize_rect(rect: Sequence[float] | None) -> tuple[int, int, int, int]:
    box = as_rect(rect)
    if not box:
        return (0, 0, 0, 0)
    return box[0], box[1], box[2], box[3]


def expand_tiny_rect(left: int, top: int, right: int, bottom: int, *, min_size: int = MIN_SIZE) -> tuple[int, int, int, int]:
    width = max(1, right - left)
    height = max(1, bottom - top)
    if width < min_size:
        cx = (left + right) / 2
        left = int(round(cx - min_size / 2))
        right = left + min_size
    if height < min_size:
        cy = (top + bottom) / 2
        top = int(round(cy - min_size / 2))
        bottom = top + min_size
    return left, top, right, bottom


def sketch_polyline(
    left: float,
    top: float,
    right: float,
    bottom: float,
    *,
    amplitude: float = DEFAULT_AMPLITUDE,
    samples_per_edge: int = 11,
    overshoot: float = 3.0,
    seed: int | None = None,
    stroke: int = 0,
) -> list[tuple[float, float]]:
    """Closed-ish wobble around the rect. Amplitude is clamped to 2–6 px."""
    amp = max(2.0, min(6.0, float(amplitude)))
    n = max(6, int(samples_per_edge))
    rng = random.Random((seed if seed is not None else 0) + stroke * 97)

    edges = (
        ((left, top), (right, top), (0.0, -1.0)),
        ((right, top), (right, bottom), (1.0, 0.0)),
        ((right, bottom), (left, bottom), (0.0, 1.0)),
        ((left, bottom), (left, top), (-1.0, 0.0)),
    )
    points: list[tuple[float, float]] = []
    for start, end, normal in edges:
        points.extend(
            _wobble_edge(start, end, normal, rng=rng, amp=amp, samples=n, overshoot=overshoot)
        )
    if points:
        points.append(points[0])
    return points


def render_sketch(
    rect: Sequence[float] | None,
    *,
    seed: int | None = None,
) -> tuple[Image.Image, int, int]:
    """RGBA image of a double-stroke pencil outline and its screen origin."""
    left, top, right, bottom = expand_tiny_rect(*normalize_rect(rect))
    width = max(1, right - left)
    height = max(1, bottom - top)
    origin_x = left - PAD
    origin_y = top - PAD
    image = Image.new("RGBA", (width + 2 * PAD, height + 2 * PAD), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    loc_l, loc_t = float(PAD), float(PAD)
    loc_r, loc_b = float(PAD + width), float(PAD + height)
    path_a = sketch_polyline(loc_l, loc_t, loc_r, loc_b, amplitude=4.8, seed=seed, stroke=0)
    path_b = sketch_polyline(loc_l, loc_t, loc_r, loc_b, amplitude=3.2, seed=seed, stroke=1)
    if path_a:
        draw.line(_as_ints(path_a), fill=(255, 196, 64, 235), width=3, joint="curve")
    if path_b:
        draw.line(_as_ints(path_b), fill=(255, 255, 255, 150), width=2, joint="curve")
    return image, origin_x, origin_y


def distance_to_rect_border(x: float, y: float, left: float, top: float, right: float, bottom: float) -> float:
    inside_x = left <= x <= right
    inside_y = top <= y <= bottom
    if inside_x and inside_y:
        return min(x - left, right - x, y - top, bottom - y)
    dx = 0.0 if inside_x else min(abs(x - left), abs(x - right))
    dy = 0.0 if inside_y else min(abs(y - top), abs(y - bottom))
    return math.hypot(dx, dy)


def _wobble_edge(
    start: tuple[float, float],
    end: tuple[float, float],
    normal: tuple[float, float],
    *,
    rng: random.Random,
    amp: float,
    samples: int,
    overshoot: float,
) -> list[tuple[float, float]]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    nx, ny = normal
    x0 = start[0] - ux * overshoot
    y0 = start[1] - uy * overshoot
    x1 = end[0] + ux * overshoot
    y1 = end[1] + uy * overshoot
    points: list[tuple[float, float]] = []
    for i in range(samples + 1):
        t = i / samples
        fade = math.sin(t * math.pi)
        wobble = (rng.random() * 2.0 - 1.0) * amp * (0.35 + 0.65 * fade)
        x = x0 + (x1 - x0) * t + nx * wobble
        y = y0 + (y1 - y0) * t + ny * wobble
        points.append((x, y))
    return points


def _as_ints(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
    return [(int(round(x)), int(round(y))) for x, y in points]
