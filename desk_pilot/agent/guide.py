"""Guide / how-to mode: sketch a target and wait for the user to act."""

from __future__ import annotations

import re
from typing import Any

from desk_pilot.desktop.rects import SKIP_NO_RECT

_GUIDE_PATTERNS = (
    r"\bhow to\b",
    r"\bhow do i\b",
    r"\bhow do you\b",
    r"\bshow me how\b",
    r"\bguide me\b",
    r"\bteach me\b",
    r"\bwalk me through\b",
    r"\bwalkthrough\b",
)

_GUIDE_RE = re.compile("|".join(_GUIDE_PATTERNS), re.IGNORECASE)

ACTION_TOOLS = frozenset(
    {"click", "type_text", "hotkey", "launch_app", "focus_window", "wait_for_window", "screenshot_region"}
)


def is_guide_goal(goal: str, *, force: bool = False) -> bool:
    """True when Settings Guide mode is on, or the goal asks how to do something."""
    if force:
        return True
    text = (goal or "").strip()
    if not text:
        return False
    return bool(_GUIDE_RE.search(text))


def instruction_for_tool(name: str, args: dict[str, Any] | None) -> str:
    args = args or {}
    if name == "guide_step":
        text = str(args.get("instruction") or "").strip()
        return text or "Do the highlighted step."
    if name == "click":
        target = args.get("name") or args.get("automation_id")
        if target:
            return f"Click “{target}”."
        if args.get("x") is not None and args.get("y") is not None:
            return f"Click at ({args.get('x')}, {args.get('y')})."
        return "Click the highlighted control."
    if name == "type_text":
        typed = args.get("text")
        where = args.get("name") or args.get("automation_id")
        if typed and where:
            return f"Type {typed!r} into “{where}”."
        if typed:
            return f"Type {typed!r} into the highlighted field."
        return "Type into the highlighted field."
    if name == "hotkey":
        keys = args.get("keys") or "the shortcut"
        return f"Press {keys}."
    if name == "launch_app":
        app = args.get("name") or "the app"
        return (
            f"Open {app}. If a window for it is already open, switch to that window "
            "instead of launching another copy."
        )
    if name == "focus_window":
        target = args.get("title_contains") or args.get("process_contains") or "the app"
        return f"Switch to the {target} window."
    if name == "wait_for_window":
        title = args.get("title_contains") or "the window"
        return f"Wait until you see a window titled like “{title}”."
    return "Do the highlighted step."


def expected_from_args(name: str, args: dict[str, Any] | None) -> dict[str, str]:
    args = args or {}
    title = str(args.get("expected_title") or args.get("title_contains") or "").strip()
    if not title and name == "launch_app":
        title = str(args.get("name") or "").strip()
    if not title and name == "focus_window":
        title = str(args.get("title_contains") or args.get("process_contains") or "").strip()
    if title:
        return {"title_contains": title}
    return {}


FALLBACK_WINDOW_NOTE = (
    "The whole window is highlighted as a fallback — the named control was not found."
)
RETRY_NEED_TARGET = (
    "guide_step needs automation_id, name, or x,y from the latest list_ui so the sketch "
    "can outline a control. Instruction-only is not enough; retry with a target."
)


def guide_has_locator(args: dict[str, Any] | None) -> bool:
    """True when the model passed a control id, visible name, or x,y."""
    args = args or {}
    if str(args.get("automation_id") or "").strip():
        return True
    if str(args.get("name") or "").strip():
        return True
    x, y = args.get("x"), args.get("y")
    return x is not None and x != "" and y is not None and y != ""


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_guide_rect(backend: Any, args: dict[str, Any] | None, *, tool_name: str = "guide_step") -> dict[str, Any]:
    """Locate a sketch rect: control / xy, then named window, then focused window."""
    from desk_pilot.desktop.rects import as_rect

    args = args or {}
    aid = str(args.get("automation_id") or "").strip() or None
    nam = str(args.get("name") or "").strip() or None
    if tool_name == "launch_app":
        nam = None
    x = _opt_int(args.get("x"))
    y = _opt_int(args.get("y"))
    title = (
        str(
            args.get("expected_title")
            or args.get("title_contains")
            or args.get("process_contains")
            or ""
        ).strip()
        or None
    )
    if tool_name == "launch_app":
        title = title or str(args.get("name") or "").strip() or None
    if tool_name == "focus_window":
        title = title or str(args.get("title_contains") or args.get("process_contains") or "").strip() or None

    has_control_query = bool(aid or nam or (x is not None and y is not None))
    box = None
    if has_control_query:
        box = as_rect(backend.find_control_rect(automation_id=aid, name=nam, x=x, y=y))
    if box:
        source = "xy" if (x is not None and y is not None and not aid and not nam) else "control"
        return {"rect": box, "source": source, "fallback": False}

    if title:
        try:
            titled = as_rect(backend.window_rect_by_title(title))
        except Exception:
            titled = None
        if titled:
            return {"rect": titled, "source": "window", "fallback": True, "title": title}

    try:
        focused = as_rect(backend.focused_window_rect())
    except Exception:
        focused = None
    if focused:
        return {"rect": focused, "source": "window", "fallback": True}
    return {"rect": None, "source": "none", "fallback": False, "error": SKIP_NO_RECT}


def snapshot_advanced(before: dict[str, Any] | None, after: dict[str, Any] | None, expected: dict[str, str] | None) -> bool:
    """Heuristic: the UI changed in a way that likely means the user did the step."""
    before = before or {}
    after = after or {}
    if after.get("launch_error") and not before.get("launch_error"):
        return False
    before_name = str((before.get("window") or {}).get("name") or "")
    after_name = str((after.get("window") or {}).get("name") or "")
    needle = str((expected or {}).get("title_contains") or "").strip().lower()
    if needle:
        if needle in after_name.lower() and needle not in before_name.lower():
            return True
        return False
    if before_name and after_name and before_name != after_name:
        return True
    return False
