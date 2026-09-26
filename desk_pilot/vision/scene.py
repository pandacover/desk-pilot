"""Compact FastVLM scene JSON: element boxes, labels, click centers."""

from __future__ import annotations

import json
import re
from typing import Any

MAX_ELEMENTS = 20
MAX_LABEL = 80
MAX_NOTE = 160

SCENE_PROMPT = (
    "Extract a compact UI scene from this screenshot of a Windows app or browser. "
    "Return ONLY JSON (no markdown, no extra text) with this exact shape: "
    '{"window":"...","region":{"x":X,"y":Y,"w":W,"h":H},"elements":['
    '{"id":"img_0","label":"...","role":"button|link|image|input|text|icon|other",'
    '"box":[left,top,right,bottom],"click":[cx,cy]}],"note":"optional short"}'
    " The crop's top-left is screen (region.x, region.y); size is region.w x region.h. "
    "box and click MUST be absolute screen pixels. Cap 12 salient elements "
    "(buttons, links, images, inputs, icons, result tiles, context-menu items, "
    "Save/Download). Skip tiny chrome noise."
)


def json_object_complete(text: str) -> bool:
    """True when ``text`` contains a balanced JSON object (string-aware)."""
    start = (text or "").find("{")
    if start < 0:
        return False
    depth = 0
    in_str = False
    escape = False
    for ch in text[start:]:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return True
            if depth < 0:
                return False
    return False


def compact_scene(scene: dict[str, Any] | None) -> str:
    payload = scene if isinstance(scene, dict) else {}
    return json.dumps(payload, ensure_ascii=False, indent=None, separators=(",", ":"))


def parse_scene_text(text: str, *, window: str = "", region: dict[str, int] | None = None) -> dict[str, Any]:
    raw = _extract_json_object(text)
    if not isinstance(raw, dict):
        note = (text or "").strip().replace("\n", " ")[:MAX_NOTE]
        return normalize_scene({}, window=window, region=region, note=note or "FastVLM returned no JSON")
    return normalize_scene(raw, window=window, region=region)


def normalize_scene(
    raw: dict[str, Any] | None,
    *,
    window: str = "",
    region: dict[str, int] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    region_out = _coerce_region(data.get("region") if isinstance(data.get("region"), dict) else None, region)
    title = str(data.get("window") or window or "").strip()[:120]
    elements_in = data.get("elements") if isinstance(data.get("elements"), list) else []
    elements: list[dict[str, Any]] = []
    for item in elements_in:
        if len(elements) >= MAX_ELEMENTS:
            break
        if not isinstance(item, dict):
            continue
        box = _coerce_box(item.get("box"), region_out)
        if not box:
            continue
        click = _coerce_click(item.get("click"), box)
        role = str(item.get("role") or "other").strip().lower()[:24] or "other"
        label = str(item.get("label") or item.get("name") or "").strip()[:MAX_LABEL]
        ident = str(item.get("id") or "").strip() or f"img_{len(elements)}"
        elements.append(
            {
                "id": ident[:24],
                "label": label,
                "role": role,
                "box": box,
                "click": click,
            }
        )
    extra = str(note or data.get("note") or "").strip()[:MAX_NOTE]
    scene: dict[str, Any] = {
        "window": title,
        "region": region_out,
        "elements": elements,
    }
    if extra:
        scene["note"] = extra
    elapsed = data.get("elapsed_ms")
    if elapsed is not None:
        try:
            scene["elapsed_ms"] = max(0, int(elapsed))
        except (TypeError, ValueError):
            pass
    if data.get("timed_out"):
        scene["timed_out"] = True
    return scene


def empty_scene(*, window: str = "", region: dict[str, int] | None = None, note: str = "") -> dict[str, Any]:
    return normalize_scene({}, window=window, region=region, note=note)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    blob = (text or "").strip()
    if not blob:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", blob, re.DOTALL)
    if fence:
        blob = fence.group(1)
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _coerce_region(raw: dict[str, Any] | None, fallback: dict[str, int] | None) -> dict[str, int]:
    src = raw or fallback or {}
    x = _as_int(src.get("x"), 0)
    y = _as_int(src.get("y"), 0)
    w = max(1, _as_int(src.get("w") if src.get("w") is not None else src.get("width"), 1))
    h = max(1, _as_int(src.get("h") if src.get("h") is not None else src.get("height"), 1))
    return {"x": x, "y": y, "w": w, "h": h}


def _coerce_box(raw: Any, region: dict[str, int]) -> list[int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        left, top, right, bottom = (float(v) for v in raw[:4])
    except (TypeError, ValueError):
        return None
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    if max(abs(left), abs(top), abs(right), abs(bottom)) <= 1.5:
        left = region["x"] + left * region["w"]
        top = region["y"] + top * region["h"]
        right = region["x"] + right * region["w"]
        bottom = region["y"] + bottom * region["h"]
    else:
        left, top, right, bottom = _maybe_offset_box(left, top, right, bottom, region)
    box = [int(round(left)), int(round(top)), int(round(right)), int(round(bottom))]
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _maybe_offset_box(left: float, top: float, right: float, bottom: float, region: dict[str, int]) -> tuple[float, float, float, float]:
    rx, ry, rw, rh = region["x"], region["y"], region["w"], region["h"]
    if rx == 0 and ry == 0:
        return left, top, right, bottom
    if left >= rx - 8 and top >= ry - 8:
        return left, top, right, bottom
    if 0 <= left <= rw + 8 and 0 <= top <= rh + 8 and 0 <= right <= rw + 8 and 0 <= bottom <= rh + 8:
        return left + rx, top + ry, right + rx, bottom + ry
    return left, top, right, bottom


def _coerce_click(raw: Any, box: list[int]) -> list[int]:
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            cx, cy = int(round(float(raw[0]))), int(round(float(raw[1])))
        except (TypeError, ValueError):
            pass
    return [cx, cy]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default
