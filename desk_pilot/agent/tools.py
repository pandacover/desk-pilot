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
                "Prefer this over screenshots except on a canvas / thin browser tree "
                "(tldraw, Figma, Paint) where UIA cannot see strokes."
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
            "name": "drag",
            "description": (
                "Auto mode only. Mouse-down at (x1,y1), move, mouse-up at (x2,y2). "
                "Use this to draw on a canvas (tldraw, Figma, Paint) when list_ui has no "
                "inkable control. Optional points=[[x,y],...] traces a polyline (rect outline "
                "or ellipse) in one stroke. You may call several drags in one turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "Start screen X (mouse down)."},
                    "y1": {"type": "integer", "description": "Start screen Y."},
                    "x2": {"type": "integer", "description": "End screen X (mouse up)."},
                    "y2": {"type": "integer", "description": "End screen Y."},
                    "points": {
                        "type": "array",
                        "description": "Optional polyline [[x,y],...] for a rect or ellipse.",
                        "items": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                    },
                },
                "required": ["x1", "y1", "x2", "y2"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_art",
            "description": (
                "Art goals only (sketch/draw/draw me a X). Create a paste-ready PNG of the "
                "subject — OpenRouter image generation if the key supports it, otherwise a "
                "simple geometric PNG/SVG. Copies the PNG to the clipboard when possible. "
                "Then focus the canvas and hotkey ctrl+v. If clipboard or paste fails, use "
                "the returned drag playbook (body rect + wheel ellipses for a car)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "What to draw, e.g. car.",
                    }
                },
                "required": ["subject"],
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
                "Send a keyboard shortcut. Examples: win+r, enter, tab, ctrl+a, alt+f4. "
                "To change a Chromium-family browser URL, call navigate instead of "
                "ctrl+l / type_text / Enter. Do NOT use ctrl+s to download a picture from a "
                "search/results page — that saves HTML. Prefer Save image as / a download control."
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
                "Capture a small screen region with mss. Use when list_ui cannot find a control, "
                "and freely in canvas mode (tldraw/Figma/thin browser tree)."
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
            "name": "navigate",
            "description": (
                "Change a Chromium-family browser window (Helium, Chrome, Edge) to a URL "
                "in one step: focus the window, type into the address bar, press Enter, "
                "and verify the title/address actually changed. If the address Edit/ComboBox "
                "is missing from the UIA tree, the same call focuses the omnibox (ctrl+l) "
                "and continues. Use this whenever the browser needs a different URL. Other "
                "browsers return nav_failed. Optional title_contains / process_contains pick "
                "the window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to load in the address bar.",
                    },
                    "title_contains": {
                        "type": "string",
                        "description": "Optional window title substring, e.g. Helium.",
                    },
                    "process_contains": {
                        "type": "string",
                        "description": "Optional process stem, e.g. helium or chrome.",
                    },
                },
                "required": ["url"],
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
            "name": "find_files",
            "description": (
                "Search the local filesystem (user profile, Desktop, Documents, Downloads, "
                "Program Files, Program Files (x86), Steam/common if present) for a file. "
                "Use this FIRST when the user wants to find/locate a file or .exe on this "
                "computer. Pass name (e.g. brawlhalla.exe) or glob (*.exe). Returns full paths "
                "with size and mtime. Do not Win+S the filename into web search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Filename or substring, e.g. brawlhalla.exe",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Optional glob, e.g. *.exe or *Brawlhalla*",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cap results (default 20, max 50).",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_file",
            "description": (
                "Check a saved path on disk. For image downloads, rejects HTML masquerading "
                "as .jpg/.png (magic bytes / size). Call this after a purported image save."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full path of the file to check.",
                    },
                    "expect": {
                        "type": "string",
                        "description": "any (default) or image / jpg / png.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guide_step",
            "description": (
                "Guide mode only: highlight one control or window and tell the human what to do. "
                "You MUST pass automation_id or visible name from list_ui, expected_title from "
                "list_windows / top_windows, or real x and y from a list_ui rect. Never pass "
                "x=0,y=0. To switch to an already-open Chrome/Helium/ChatGPT window, pass name or "
                "expected_title matching that window — the sketch uses its bounds. "
                "Instruction-only calls are rejected and must be retried. Do not click or type. "
                "One step per turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "Short instruction, e.g. Click the address bar.",
                    },
                    "automation_id": {
                        "type": "string",
                        "description": "UIA AutomationId from the latest list_ui. Required unless name or x,y is set.",
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Visible Name from list_ui. Case-insensitive; a contains match is used "
                            "if the exact name is missing."
                        ),
                    },
                    "x": {
                        "type": "integer",
                        "description": "Screen X when no control id/name. Must be a real list_ui coordinate, never 0.",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Screen Y when no control id/name. Must be a real list_ui coordinate, never 0.",
                    },
                    "expected_title": {
                        "type": "string",
                        "description": (
                            "Window title from list_windows / top_windows (e.g. ChatGPT, Helium). "
                            "Use this to sketch that window. Also used to auto-advance after the user acts."
                        ),
                    },
                },
                "required": ["instruction"],
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


GUIDE_TOOL_NAMES = frozenset({"list_ui", "list_windows", "guide_step", "done", "fail"})
GUIDE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    item for item in TOOL_DEFINITIONS if item["function"]["name"] in GUIDE_TOOL_NAMES
]
# drag / prepare_art are never offered in guide mode and must not execute if hallucinated.
AUTO_ONLY_TOOLS = frozenset({"drag", "prepare_art", "find_files", "verify_file"})


class TerminalCall:
    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.name = name
        self.payload = payload


def dispatch_tool(
    backend: DesktopBackend,
    name: str,
    arguments: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | TerminalCall:
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
    if name == "drag":
        try:
            x1, y1, x2, y2 = int(args["x1"]), int(args["y1"]), int(args["x2"]), int(args["y2"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"drag needs x1,y1,x2,y2: {exc}"}
        return backend.drag(x1, y1, x2, y2, points=args.get("points") if isinstance(args.get("points"), list) else None)
    if name == "prepare_art":
        from desk_pilot.agent.art import run_prepare_art, subject_from_args

        subject = subject_from_args(args)
        if not subject:
            return {"ok": False, "error": "prepare_art requires subject."}
        generate = None
        llm = extra.get("llm") if extra else None
        if llm is not None and hasattr(llm, "generate_image"):
            generate = llm.generate_image
        return run_prepare_art(
            subject,
            generate_image=generate,
            window_rect=extra.get("window_rect") if extra else None,
            backend=backend,
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
    if name == "navigate":
        from desk_pilot.agent.nav import run_navigate

        url = _opt_str(args.get("url")) or _opt_str(args.get("text"))
        log = extra.get("log") if extra else None
        return run_navigate(
            backend,
            url or "",
            title_contains=_opt_str(args.get("title_contains")),
            process_contains=_opt_str(args.get("process_contains")),
            log=log,
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
    if name == "find_files":
        max_results = args.get("max_results", 20)
        try:
            limit = int(max_results)
        except (TypeError, ValueError):
            limit = 20
        return backend.find_files(
            name=_opt_str(args.get("name")),
            glob=_opt_str(args.get("glob")),
            max_results=limit,
        )
    if name == "verify_file":
        from desk_pilot.desktop.files import verify_file

        return verify_file(_opt_str(args.get("path")), expect=_opt_str(args.get("expect")) or "any")
    if name == "guide_step":
        return _guide_step_payload(backend, args)
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


def _guide_step_payload(backend: DesktopBackend, args: dict[str, Any]) -> dict[str, Any]:
    from desk_pilot.agent.guide import (
        FALLBACK_WINDOW_NOTE,
        RETRY_NEED_TARGET,
        guide_has_locator,
        instruction_for_tool,
        resolve_guide_rect,
    )

    instruction = instruction_for_tool("guide_step", args)
    if not guide_has_locator(args):
        return {
            "ok": False,
            "guide": True,
            "instruction": instruction,
            "rect": None,
            "skip": True,
            "error": RETRY_NEED_TARGET,
        }
    resolved = resolve_guide_rect(backend, args, tool_name="guide_step")
    rect = resolved.get("rect")
    if resolved.get("fallback") and rect:
        instruction = f"{instruction} {FALLBACK_WINDOW_NOTE}".strip()
    return {
        "ok": True,
        "guide": True,
        "instruction": instruction,
        "rect": rect,
        "source": resolved.get("source"),
        "fallback": bool(resolved.get("fallback")),
        "expected_title": _opt_str(args.get("expected_title")),
        "error": resolved.get("error"),
    }
