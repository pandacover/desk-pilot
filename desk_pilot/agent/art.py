"""Create-then-insert art: OpenRouter image if the key allows it, else a geometric PNG."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from desk_pilot.agent.canvas import art_subject, stroke_playbook
from desk_pilot.app.config import screenshot_dir
from desk_pilot.desktop.clipboard import copy_png_to_clipboard


GenerateFn = Callable[[str], dict[str, Any]]


def run_prepare_art(
    subject: str,
    *,
    generate_image: GenerateFn | None = None,
    window_rect: list[int] | None = None,
    backend: Any | None = None,
) -> dict[str, Any]:
    """Write a paste-ready PNG (and SVG). Clipboard copy is best-effort."""
    topic = (subject or "").strip() or "sketch"
    gen_error = ""
    method = "geometric"
    path: Path | None = None

    if generate_image is not None:
        try:
            generated = generate_image(_image_prompt(topic))
        except Exception as exc:  # noqa: BLE001
            generated = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if generated.get("ok") and generated.get("path"):
            candidate = Path(str(generated["path"]))
            if candidate.is_file():
                path = candidate
                method = str(generated.get("method") or "openrouter")
        else:
            gen_error = str(generated.get("error") or "OpenRouter image generation is not available.")
    else:
        gen_error = "No image-generation client; using a geometric PNG."

    if path is None:
        path, svg_path = render_geometric_art(topic)
    else:
        svg_path = write_subject_svg(topic)

    clip = copy_png_to_clipboard(path)
    if backend is not None and hasattr(backend, "set_clipboard_image"):
        try:
            backend.set_clipboard_image(str(path))
        except Exception:
            pass

    playbook = stroke_playbook(topic, window_rect)
    clipboard_ok = bool(clip.get("ok"))
    hint = (
        "PNG is on the clipboard. Focus the canvas and hotkey ctrl+v."
        if clipboard_ok
        else (
            str(clip.get("error") or "Clipboard paste failed.")
            + " Fall back to the geometric drag playbook (several drag calls in one turn)."
        )
    )
    return {
        "ok": True,
        "subject": topic,
        "path": str(path),
        "svg_path": str(svg_path) if svg_path else None,
        "method": method,
        "clipboard": clipboard_ok,
        "clipboard_error": None if clipboard_ok else clip.get("error"),
        "generate_error": gen_error or None,
        "playbook": playbook,
        "hint": hint,
    }


def render_geometric_art(subject: str) -> tuple[Path, Path]:
    """Simple side-view icon PNG + SVG. Always works offline (Pillow)."""
    from PIL import Image, ImageDraw

    kind = (subject or "sketch").strip().lower()
    folder = screenshot_dir()
    stamp = int(time.time() * 1000)
    png_path = folder / f"art_{stamp}.png"
    svg_path = folder / f"art_{stamp}.svg"
    image = Image.new("RGBA", (512, 320), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    if "car" in kind or "auto" in kind or "vehicle" in kind:
        _draw_car(draw)
        svg = _car_svg()
    else:
        _draw_blob(draw)
        svg = _blob_svg(kind)
    image.save(png_path)
    svg_path.write_text(svg, encoding="utf-8")
    return png_path, svg_path


def write_subject_svg(subject: str) -> Path:
    folder = screenshot_dir()
    path = folder / f"art_{int(time.time() * 1000)}.svg"
    kind = (subject or "sketch").strip().lower()
    path.write_text(_car_svg() if "car" in kind else _blob_svg(kind), encoding="utf-8")
    return path


def _image_prompt(subject: str) -> str:
    return (
        f"Simple flat icon of a {subject} on a white background, no text, "
        "bold outlines, sticker style, centered."
    )


def _draw_car(draw: Any) -> None:
    body = [70, 150, 440, 230]
    cabin = [180, 80, 380, 160]
    draw.rounded_rectangle(body, radius=18, outline=(30, 30, 30, 255), width=8, fill=(70, 130, 200, 230))
    draw.polygon([(190, 155), (210, 90), (360, 90), (380, 155)], outline=(30, 30, 30, 255), fill=(200, 230, 255, 230))
    draw.rounded_rectangle(cabin, radius=8, outline=(30, 30, 30, 255), width=6)
    for cx in (140, 370):
        draw.ellipse([cx - 38, 210, cx + 38, 286], outline=(20, 20, 20, 255), width=8, fill=(40, 40, 40, 255))
        draw.ellipse([cx - 14, 234, cx + 14, 262], outline=(180, 180, 180, 255), width=3, fill=(200, 200, 200, 255))


def _draw_blob(draw: Any) -> None:
    draw.ellipse([120, 60, 390, 260], outline=(30, 30, 30, 255), width=8, fill=(255, 210, 80, 230))


def _car_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" width="512" height="320" viewBox="0 0 512 320">
  <rect x="70" y="150" width="370" height="80" rx="18" fill="#4682c8" stroke="#1e1e1e" stroke-width="8"/>
  <polygon points="190,155 210,90 360,90 380,155" fill="#c8e6ff" stroke="#1e1e1e" stroke-width="6"/>
  <circle cx="140" cy="248" r="38" fill="#282828" stroke="#141414" stroke-width="8"/>
  <circle cx="370" cy="248" r="38" fill="#282828" stroke="#141414" stroke-width="8"/>
</svg>
"""


def _blob_svg(kind: str) -> str:
    label = (kind or "sketch").replace("&", "")[:24]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="320" viewBox="0 0 512 320">
  <ellipse cx="256" cy="160" rx="135" ry="100" fill="#ffd250" stroke="#1e1e1e" stroke-width="8"/>
  <text x="256" y="168" text-anchor="middle" font-size="28" fill="#1e1e1e">{label}</text>
</svg>
"""


def subject_from_args(args: dict[str, Any] | None, goal: str = "") -> str:
    args = args or {}
    text = str(args.get("subject") or args.get("text") or "").strip()
    return text or art_subject(goal)
