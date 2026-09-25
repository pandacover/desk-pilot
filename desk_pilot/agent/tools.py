from __future__ import annotations

import json
from typing import Any

from desk_pilot.desktop.base import DesktopBackend

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_ui",
            "description": (
                "Return a compact UI Automation tree for the focused window "
                "(name, type, automation_id, bounding rect, short path). "
                "Prefer this over screenshots."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_depth": {
                        "type": "integer",
                        "description": "Walk depth from the focused window. Default 5.",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": (
                "Click a control in the focused window by automation_id or visible name, "
                "or click screen coordinates if UIA cannot find it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "automation_id": {"type": "string"},
                    "name": {"type": "string", "description": "Visible Name property; case-insensitive."},
                    "x": {"type": "integer", "description": "Screen X, used when no control id/name."},
                    "y": {"type": "integer", "description": "Screen Y, used when no control id/name."},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type literal text into the focused control, or a named/id target first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "automation_id": {"type": "string"},
                    "name": {"type": "string"},
                    "clear": {
                        "type": "boolean",
                        "description": "If true, select-all/clear before typing.",
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "description": (
                "Send a keyboard shortcut. Examples: win+r, enter, ctrl+s, alt+f4, ctrl+a, tab."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Plus-separated chord, e.g. ctrl+s"}
                },
                "required": ["keys"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot_region",
            "description": (
                "Capture a small screen region with mss. Use only when list_ui cannot find a control."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["x", "y", "width", "height"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": (
                "List top-level windows (title, process) including ones that are not focused. "
                "Use this before launch_app to reuse an already-open instance."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": (
                "Activate an already-open top-level window by title substring or process "
                "name. Prefer this over launch_app when the app is already running."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title_contains": {
                        "type": "string",
                        "description": "Substring of the window title, e.g. Helium or Notepad.",
                    },
                    "process_contains": {
                        "type": "string",
                        "description": "Substring of the process stem, e.g. helium or notepad.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": (
                "Start an installed Windows app by display name or executable only if it is "
                "not already running. If a matching window exists, it is focused instead "
                "(reused=true) and no second instance is started. Searches PATH, registry "
                "App Paths, Start Menu .lnk files, and shell:AppsFolder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Visible app name or exe stem, e.g. Helium, chrome, notepad.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for_window",
            "description": "Wait until the foreground window title contains the given text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_contains": {"type": "string"},
                    "timeout_seconds": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "End the run successfully with a short result for the user.",
            "parameters": {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fail",
            "description": "End the run because the goal cannot be completed.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]


class TerminalCall:
    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.name = name
        self.payload = payload


def dispatch_tool(backend: DesktopBackend, name: str, arguments: dict[str, Any]) -> dict[str, Any] | TerminalCall:
    args = arguments or {}
    if name == "list_ui":
        return backend.list_ui(max_depth=int(args.get("max_depth") or 5))
    if name == "click":
        x = args.get("x")
        y = args.get("y")
        return backend.click(
            automation_id=_opt_str(args.get("automation_id")),
            name=_opt_str(args.get("name")),
            x=int(x) if x is not None and x != "" else None,
            y=int(y) if y is not None and y != "" else None,
        )
    if name == "type_text":
        text = args.get("text")
        if text is None:
            return {"ok": False, "error": "type_text requires text."}
        return backend.type_text(
            str(text),
            automation_id=_opt_str(args.get("automation_id")),
            name=_opt_str(args.get("name")),
            clear=bool(args.get("clear") or False),
        )
    if name == "hotkey":
        keys = args.get("keys")
        if not keys:
            return {"ok": False, "error": "hotkey requires keys."}
        return backend.hotkey(str(keys))
    if name == "screenshot_region":
        try:
            return backend.screenshot_region(
                int(args["x"]),
                int(args["y"]),
                int(args["width"]),
                int(args["height"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"screenshot_region needs x,y,width,height: {exc}"}
    if name == "list_windows":
        return backend.list_windows()
    if name == "focus_window":
        return backend.focus_window(
            title_contains=_opt_str(args.get("title_contains")),
            process_contains=_opt_str(args.get("process_contains")),
        )
    if name == "launch_app":
        app = _opt_str(args.get("name"))
        if not app:
            return {"ok": False, "error": "launch_app requires name."}
        return backend.launch_app(app)
    if name == "wait_for_window":
        timeout = args.get("timeout_seconds", 8)
        try:
            timeout_f = float(timeout)
        except (TypeError, ValueError):
            timeout_f = 8.0
        return backend.wait_for_window(
            title_contains=_opt_str(args.get("title_contains")),
            timeout_seconds=timeout_f,
        )
    if name == "done":
        return TerminalCall("done", {"result": str(args.get("result") or "Done.")})
    if name == "fail":
        return TerminalCall("fail", {"reason": str(args.get("reason") or "Failed.")})
    return {"ok": False, "error": f"Unknown tool: {name}"}


def compact_json(data: Any, limit: int = 12000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "…[truncated]"


def parse_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}
    return value if isinstance(value, dict) else {"value": value}


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
