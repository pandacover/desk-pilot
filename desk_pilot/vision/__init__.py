"""Local FastVLM sidecar. The UI process must not import torch."""

from __future__ import annotations

import re

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MODEL_ID = "apple/FastVLM-0.5B"
MAX_ELEMENTS = 20
# FastVLM-0.5B native crop is 1024²; shrinking before encode is the CPU win.
SCENE_MAX_EDGE = 768
SCENE_MAX_EDGE_CPU = 512
# Compact scene JSON is small. Official HF snippet uses 128; we were at 512 (minutes on CPU).
SCENE_MAX_NEW_TOKENS = 192
HEALTH_STATUSES = ("loading", "ready", "busy", "error")

_MISSING_PKG_RES = (
    re.compile(r"pip install ([A-Za-z0-9_.\-]+)"),
    re.compile(r"No module named ['\"]([^'\"]+)['\"]"),
    re.compile(r"requires (?:the )?([A-Za-z0-9_.\-]+) library", re.I),
)


def missing_package_name(text: str) -> str | None:
    """Best-effort package name from a transformers/ImportError blob."""
    blob = text or ""
    for pattern in _MISSING_PKG_RES:
        match = pattern.search(blob)
        if match:
            name = match.group(1).split(".")[0]
            if name.lower() not in {"the", "a", "an"}:
                return name
    return None


def short_health_error(text: str, *, limit: int = 48) -> str:
    """Compact chip text for GET /health error. Prefers 'missing timm' over a wall of ImportError."""
    pkg = missing_package_name(text)
    if pkg:
        return f"missing {pkg}"
    err = " ".join((text or "").split())
    if not err:
        return "vision error"
    if len(err) <= limit:
        return err
    return "vision error"
