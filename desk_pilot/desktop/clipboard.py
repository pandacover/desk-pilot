"""Copy a PNG onto the clipboard so the agent can paste into a canvas app."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def copy_png_to_clipboard(path: str | Path) -> dict[str, Any]:
    """Windows CF_DIB paste. Off Windows this is a documented stub failure."""
    file = Path(path)
    if not file.is_file():
        return {"ok": False, "error": f"No PNG at {path}."}
    if sys.platform != "win32":
        return {
            "ok": False,
            "error": (
                "Clipboard PNG paste is Windows-only in this build. "
                "Use the geometric drag playbook instead."
            ),
            "stub": True,
        }
    try:
        return _copy_dib_win32(file)
    except Exception as exc:  # noqa: BLE001 — clipboard is best-effort
        return {"ok": False, "error": f"Clipboard PNG failed: {exc}"}


def _copy_dib_win32(file: Path) -> dict[str, Any]:
    import ctypes
    import io

    from PIL import Image

    image = Image.open(file).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, "BMP")
    dib = buf.getvalue()[14:]
    if not dib:
        return {"ok": False, "error": "Could not encode DIB for the clipboard."}

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    GMEM_MOVEABLE = 0x0002
    CF_DIB = 8

    if not user32.OpenClipboard(None):
        return {"ok": False, "error": "OpenClipboard failed."}
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
        if not handle:
            return {"ok": False, "error": "GlobalAlloc failed."}
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return {"ok": False, "error": "GlobalLock failed."}
        ctypes.memmove(locked, dib, len(dib))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(handle)
            return {"ok": False, "error": "SetClipboardData(CF_DIB) failed."}
    finally:
        user32.CloseClipboard()
    return {"ok": True, "format": "CF_DIB", "bytes": len(dib)}
