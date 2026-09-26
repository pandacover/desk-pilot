"""Crop the focused window/content region and compress it for FastVLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desk_pilot.agent.canvas import content_box, is_browser_or_whiteboard, window_rect_from_snapshot
from desk_pilot.desktop.rects import sketchable_rect
from desk_pilot.vision import SCENE_MAX_EDGE


def region_from_box(box: list[int] | None) -> dict[str, int] | None:
    if not box or len(box) < 4:
        return None
    left, top, right, bottom = (int(v) for v in box[:4])
    width, height = max(1, right - left), max(1, bottom - top)
    return {"x": left, "y": top, "w": width, "h": height}


def content_region(snapshot: dict[str, Any] | None, window_rect: list[int] | None) -> list[int] | None:
    """Screen rect to capture: inset past browser chrome when the tree is a webpage."""
    box = sketchable_rect(window_rect) or window_rect_from_snapshot(snapshot)
    if not box:
        return None
    if snapshot and is_browser_or_whiteboard(snapshot):
        inner = content_box(box)
        if inner:
            return inner
    return box


def resolve_capture_box(backend: Any, snapshot: dict[str, Any] | None = None) -> list[int] | None:
    box = None
    if snapshot:
        box = window_rect_from_snapshot(snapshot)
    if not box:
        try:
            box = backend.focused_window_rect()
        except Exception:
            box = None
    return content_region(snapshot, box)


def fit_max_edge(image: Any, max_edge: int) -> Any:
    """Downscale so the longer side is at most ``max_edge`` (no upscale)."""
    edge = max(1, int(max_edge))
    size = getattr(image, "size", None)
    if not size or len(size) < 2:
        return image
    width, height = int(size[0]), int(size[1])
    if max(width, height) <= edge:
        return image
    copy = image.copy()
    copy.thumbnail((edge, edge))
    return copy


def compress_screenshot(path: str | Path, *, max_edge: int = SCENE_MAX_EDGE, quality: int = 70) -> str:
    """JPEG shrink for the sidecar. Falls back to the original path on failure."""
    src = Path(path)
    if not src.is_file():
        return str(src)
    try:
        from PIL import Image
    except Exception:
        return str(src)
    try:
        image = fit_max_edge(Image.open(src).convert("RGB"), max_edge)
        dest = src.with_name(src.stem + ".scene.jpg")
        image.save(dest, "JPEG", quality=max(40, min(int(quality), 90)), optimize=True)
        return str(dest)
    except Exception:
        return str(src)


def capture_content_screenshot(
    backend: Any,
    snapshot: dict[str, Any] | None = None,
    *,
    box: list[int] | None = None,
) -> dict[str, Any]:
    """mss/mock crop of the focused content region, compressed for FastVLM."""
    window = ""
    if isinstance(snapshot, dict):
        win = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
        window = str((win or {}).get("name") or "")
    capture_box = box or resolve_capture_box(backend, snapshot)
    region = region_from_box(capture_box)
    if not region:
        return {"ok": False, "error": "No window rect to capture.", "window": window}
    try:
        shot = backend.screenshot_region(region["x"], region["y"], region["w"], region["h"])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"screenshot failed: {exc}", "window": window, "region": region}
    if not isinstance(shot, dict) or not shot.get("ok") or not shot.get("path"):
        err = (shot or {}).get("error") if isinstance(shot, dict) else "screenshot failed"
        return {"ok": False, "error": err or "screenshot failed", "window": window, "region": region}
    compressed = compress_screenshot(str(shot["path"]))
    return {
        "ok": True,
        "path": compressed,
        "raw_path": str(shot["path"]),
        "window": window,
        "region": region,
        "box": capture_box,
    }
