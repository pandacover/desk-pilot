"""Art/canvas mode: drag + prepare_art when the user asked to sketch/draw.

Thin browser trees no longer auto-flip into this mode — FastVLM scene JSON
covers visual pages. Keep the helpers for content-region crop and playbooks.
"""

from __future__ import annotations

import math
import re
from typing import Any

# tldraw's live tree is ~10 chrome controls; treat anything this sparse as a canvas.
THIN_TREE_MAX_CONTROLS = 16

_ART_GOAL_RE = re.compile(
    r"\b(sketch|draw|paint|doodle|tldraw|figma|excalidraw|miro|whiteboard|canvas|illustrat)\b"
    r"|\bdraw me\b|\bsketch me\b|\bpaint me\b|\bdoodle me\b",
    re.IGNORECASE,
)

_CANVAS_WINDOW_HINTS = (
    "tldraw",
    "figma",
    "miro",
    "whiteboard",
    "paint",
    "excalidraw",
    "canva",
    "photoshop",
    "krita",
    "canvas",
    "klecks",
    "photopea",
)

_BROWSER_HINTS = (
    "chrome",
    "helium",
    "msedge",
    "edge",
    "firefox",
    "brave",
    "opera",
    "chromium",
    "safari",
    "arc",
    "chrome_widgetwin",
    "mozilla",
)

_ART_SUBJECT_RE = re.compile(
    r"\b(?:sketch|draw|paint|doodle)(?:\s+me)?(?:\s+a|\s+an|\s+the)?\s+([a-z][a-z0-9 \-]{1,40})",
    re.IGNORECASE,
)


def is_art_goal(goal: str) -> bool:
    """True when the user asked to sketch/draw/paint, or named a canvas app."""
    text = (goal or "").strip()
    if not text:
        return False
    return bool(_ART_GOAL_RE.search(text))


def art_subject(goal: str) -> str:
    """Best-effort subject ('car' from 'sketch me a car')."""
    text = (goal or "").strip()
    match = _ART_SUBJECT_RE.search(text)
    if match:
        subject = re.sub(r"\s+", " ", match.group(1)).strip(" .")
        subject = re.split(r"\b(?:in|on|with|using|via|inside|and)\b", subject, maxsplit=1)[0]
        subject = subject.strip(" .")
        if subject:
            return subject[:40]
    if re.search(r"\bcar\b", text, re.IGNORECASE):
        return "car"
    return "sketch"


def is_browser_or_whiteboard(snapshot: dict[str, Any] | None) -> bool:
    blob = _window_blob(snapshot)
    return any(hint in blob for hint in _CANVAS_WINDOW_HINTS + _BROWSER_HINTS)


def control_count(snapshot: dict[str, Any] | None) -> int:
    if not snapshot:
        return 0
    controls = snapshot.get("controls")
    if isinstance(controls, list):
        return len(controls)
    return 0


def is_thin_tree(snapshot: dict[str, Any] | None, *, max_controls: int = THIN_TREE_MAX_CONTROLS) -> bool:
    if not snapshot or snapshot.get("com_error"):
        return False
    return control_count(snapshot) <= max_controls


def should_use_canvas_mode(goal: str, snapshot: dict[str, Any] | None = None) -> bool:
    """Art/sketch/draw goals only. Thin UIA trees use FastVLM scene observe instead."""
    del snapshot  # kept so callers that pass a tree do not break
    return is_art_goal(goal)


def window_rect_from_snapshot(snapshot: dict[str, Any] | None) -> list[int] | None:
    from desk_pilot.desktop.rects import sketchable_rect

    if not snapshot:
        return None
    window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
    box = sketchable_rect((window or {}).get("rect"))
    if box:
        return box
    for item in snapshot.get("controls") or []:
        if not isinstance(item, dict):
            continue
        if (item.get("type") or "") not in {"Window", "Pane", "Document"}:
            continue
        box = sketchable_rect(item.get("rect"))
        if box:
            return box
    return None


def content_box(window_rect: list[int] | None) -> list[int] | None:
    """Inset past typical browser chrome so strokes land on the canvas."""
    if not window_rect or len(window_rect) < 4:
        return None
    left, top, right, bottom = (int(v) for v in window_rect[:4])
    width = right - left
    height = bottom - top
    if width < 80 or height < 80:
        return [left, top, right, bottom]
    inset_l = min(90, max(24, width // 10))
    inset_t = min(140, max(80, height // 6))
    inset_r = min(40, max(16, width // 16))
    inset_b = min(50, max(20, height // 14))
    inner = [left + inset_l, top + inset_t, right - inset_r, bottom - inset_b]
    if inner[2] - inner[0] < 40 or inner[3] - inner[1] < 40:
        return [left, top, right, bottom]
    return inner


def map_rel(box: list[int], rx1: float, ry1: float, rx2: float, ry2: float) -> list[int]:
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    return [
        int(left + width * rx1),
        int(top + height * ry1),
        int(left + width * rx2),
        int(top + height * ry2),
    ]


def ellipse_points(cx: int, cy: int, rx: int, ry: int, *, n: int = 16) -> list[list[int]]:
    rx = max(4, abs(rx))
    ry = max(4, abs(ry))
    n = max(8, min(int(n), 32))
    points: list[list[int]] = []
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        points.append([int(round(cx + rx * math.cos(t))), int(round(cy + ry * math.sin(t)))])
    return points


def rect_outline_points(left: int, top: int, right: int, bottom: int) -> list[list[int]]:
    return [
        [left, top],
        [right, top],
        [right, bottom],
        [left, bottom],
        [left, top],
    ]


def stroke_playbook(subject: str, window_rect: list[int] | None) -> list[dict[str, Any]]:
    """Absolute drag strokes for a recognizable geometric icon (car is first-class)."""
    box = content_box(window_rect) or content_box([80, 40, 1280, 800])
    if not box:
        return []
    kind = (subject or "sketch").strip().lower()
    if "car" in kind or "auto" in kind or "vehicle" in kind:
        return _car_strokes(box)
    return _blob_strokes(box, kind)


def _car_strokes(box: list[int]) -> list[dict[str, Any]]:
    body = map_rel(box, 0.18, 0.48, 0.82, 0.70)
    cabin = map_rel(box, 0.38, 0.30, 0.72, 0.50)
    rear = map_rel(box, 0.26, 0.64, 0.40, 0.86)
    front = map_rel(box, 0.60, 0.64, 0.74, 0.86)
    strokes = [
        {
            "name": "body",
            "hint": "side-view body rectangle",
            "x1": body[0],
            "y1": body[1],
            "x2": body[2],
            "y2": body[3],
            "points": rect_outline_points(*body),
        },
        {
            "name": "cabin",
            "hint": "cabin on top of the body",
            "x1": cabin[0],
            "y1": cabin[1],
            "x2": cabin[2],
            "y2": cabin[3],
            "points": rect_outline_points(*cabin),
        },
    ]
    for name, wheel in (("wheel_rear", rear), ("wheel_front", front)):
        cx = (wheel[0] + wheel[2]) // 2
        cy = (wheel[1] + wheel[3]) // 2
        rx = max(8, (wheel[2] - wheel[0]) // 2)
        ry = max(8, (wheel[3] - wheel[1]) // 2)
        pts = ellipse_points(cx, cy, rx, ry)
        strokes.append(
            {
                "name": name,
                "hint": "wheel ellipse",
                "x1": pts[0][0],
                "y1": pts[0][1],
                "x2": pts[-1][0],
                "y2": pts[-1][1],
                "points": pts,
            }
        )
    return strokes


def _blob_strokes(box: list[int], kind: str) -> list[dict[str, Any]]:
    body = map_rel(box, 0.30, 0.30, 0.70, 0.70)
    cx = (body[0] + body[2]) // 2
    cy = (body[1] + body[3]) // 2
    rx = max(12, (body[2] - body[0]) // 2)
    ry = max(12, (body[3] - body[1]) // 2)
    pts = ellipse_points(cx, cy, rx, ry)
    return [
        {
            "name": "icon",
            "hint": f"simple outline for {kind or 'subject'}",
            "x1": pts[0][0],
            "y1": pts[0][1],
            "x2": pts[-1][0],
            "y2": pts[-1][1],
            "points": pts,
        }
    ]


def playbook_hint(subject: str, window_rect: list[int] | None) -> str:
    strokes = stroke_playbook(subject, window_rect)
    if not strokes:
        return (
            "Canvas mode: list_ui is chrome-only. Use FastVLM scene JSON, then drag. "
            "You may emit several drag calls in this turn (body, then wheels)."
        )
    lines = [
        "Geometric drag playbook (absolute screen coords; emit these drag calls in one turn):",
    ]
    for i, stroke in enumerate(strokes, start=1):
        pts = stroke.get("points") or []
        if pts:
            lines.append(
                f"{i}. drag {stroke['name']} ({stroke['hint']}): points={pts[:20]}"
            )
        else:
            lines.append(
                f"{i}. drag {stroke['name']} ({stroke['hint']}): "
                f"({stroke['x1']},{stroke['y1']}) -> ({stroke['x2']},{stroke['y2']})"
            )
    lines.append(
        "Prefer prepare_art + ctrl+v if the board accepts images; otherwise run this playbook."
    )
    return "\n".join(lines)


def _window_blob(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    parts: list[str] = []
    window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
    for key in ("name", "class", "process", "automation_id"):
        parts.append(str((window or {}).get(key) or ""))
    focused = snapshot.get("focused") if isinstance(snapshot.get("focused"), dict) else {}
    parts.append(str((focused or {}).get("name") or ""))
    for item in (snapshot.get("top_windows") or [])[:8]:
        if isinstance(item, dict):
            parts.append(str(item.get("name") or ""))
            parts.append(str(item.get("process") or ""))
    for item in (snapshot.get("controls") or [])[:12]:
        if isinstance(item, dict):
            parts.append(str(item.get("name") or ""))
            parts.append(str(item.get("type") or ""))
            parts.append(str(item.get("path") or ""))
    return " ".join(parts).lower()
