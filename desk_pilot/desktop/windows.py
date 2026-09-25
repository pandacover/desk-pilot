"""Windows UI Automation backend (uiautomation + pynput/mss fallback)."""

from __future__ import annotations

import time
from typing import Any

from desk_pilot.app.config import screenshot_dir
from desk_pilot.desktop.base import DesktopBackend

_INTERACTIVE = {
    "ButtonControl",
    "EditControl",
    "DocumentControl",
    "MenuItemControl",
    "MenuBarControl",
    "MenuControl",
    "ComboBoxControl",
    "ListItemControl",
    "ListControl",
    "TreeItemControl",
    "TreeControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "TabItemControl",
    "TabControl",
    "HyperlinkControl",
    "SplitButtonControl",
    "DataItemControl",
    "DataGridControl",
    "ToolBarControl",
    "SliderControl",
    "SpinnerControl",
    "WindowControl",
    "CustomControl",
    "TextControl",
    "ComboBoxControl",
}


def _rect_list(rect: Any) -> list[int]:
    try:
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    except Exception:
        return [0, 0, 0, 0]


def _short_type(control_type_name: str) -> str:
    return (control_type_name or "Control").removesuffix("Control") or "Control"


def _escape_sendkeys(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch == "{":
            out.append("{{")
        elif ch == "}":
            out.append("{}}")
        else:
            out.append(ch)
    return "".join(out)


def _parse_hotkey(keys: str) -> list[str]:
    raw = (keys or "").strip().lower().replace(" ", "")
    if not raw:
        return []
    parts = [p for p in raw.split("+") if p]
    aliases = {
        "control": "ctrl",
        "ctl": "ctrl",
        "cmd": "win",
        "meta": "win",
        "windows": "win",
        "return": "enter",
        "esc": "escape",
        "opt": "alt",
        "option": "alt",
    }
    return [aliases.get(p, p) for p in parts]


def _to_sendkeys(parts: list[str]) -> str:
    special = {
        "ctrl": "{Ctrl}",
        "alt": "{Alt}",
        "shift": "{Shift}",
        "win": "{Win}",
        "enter": "{Enter}",
        "tab": "{Tab}",
        "escape": "{Esc}",
        "esc": "{Esc}",
        "backspace": "{Backspace}",
        "delete": "{Delete}",
        "space": "{Space}",
        "up": "{Up}",
        "down": "{Down}",
        "left": "{Left}",
        "right": "{Right}",
        "home": "{Home}",
        "end": "{End}",
        "pageup": "{PageUp}",
        "pagedown": "{PageDown}",
        "f1": "{F1}",
        "f2": "{F2}",
        "f3": "{F3}",
        "f4": "{F4}",
        "f5": "{F5}",
        "f6": "{F6}",
        "f7": "{F7}",
        "f8": "{F8}",
        "f9": "{F9}",
        "f10": "{F10}",
        "f11": "{F11}",
        "f12": "{F12}",
    }
    hold = {"ctrl", "alt", "shift", "win"}
    holds = [special[p] for p in parts if p in hold]
    rest = [p for p in parts if p not in hold]
    body = "".join(special.get(p, p) for p in rest)
    return "".join(holds) + body


class WindowsDesktop(DesktopBackend):
    dry_run = False

    def __init__(self) -> None:
        try:
            import uiautomation as auto
        except ImportError as exc:
            raise RuntimeError(
                "uiautomation is required on Windows. pip install uiautomation"
            ) from exc
        self.auto = auto
        # Timeout only — do not create the IUIAutomation COM singleton here.
        # The GUI constructs this object on the Tk thread; UIA must be bound on
        # the worker that actually calls list_ui/click/type.
        inner = getattr(auto, "uiautomation", auto)
        inner.SetGlobalSearchTimeout(3)
        self._start_apps_cache: list[dict[str, str]] | None = None

    def _prepare(self) -> dict[str, Any] | None:
        from desk_pilot.desktop.com import bind_uia_to_this_thread

        result = bind_uia_to_this_thread(self.auto)
        inner = getattr(self.auto, "uiautomation", None)
        if inner is not None and inner is not self.auto:
            bind_uia_to_this_thread(inner)
        if not result.get("ok"):
            return {
                "ok": False,
                "com_error": True,
                "error": result.get("error") or "COM initialization failed on this thread.",
                "window": {"name": ""},
                "controls": [],
            }
        return None

    def list_ui(self, max_depth: int = 5, max_controls: int = 70) -> dict[str, Any]:
        from desk_pilot.desktop.com import ensure_com, looks_like_com_error

        prep = self._prepare()
        if prep:
            return prep
        try:
            return self._list_ui_inner(max_depth=max_depth, max_controls=max_controls)
        except Exception as exc:
            if looks_like_com_error(exc):
                ensure_com(force=True)
                self._prepare()
                try:
                    return self._list_ui_inner(max_depth=max_depth, max_controls=max_controls)
                except Exception as exc2:
                    return self._com_failure(exc2)
            return self._com_failure(exc) if looks_like_com_error(exc) else {
                "ok": False,
                "error": f"Could not read foreground window: {exc}",
                "window": {"name": ""},
                "controls": [],
            }

    def _list_ui_inner(self, max_depth: int = 5, max_controls: int = 70) -> dict[str, Any]:
        from desk_pilot.desktop.com import looks_like_com_error
        from desk_pilot.desktop.launch import detect_run_not_found

        auto = self.auto
        max_depth = max(1, min(int(max_depth), 8))
        max_controls = max(8, min(int(max_controls), 120))
        try:
            window = auto.GetForegroundControl()
        except Exception as exc:
            payload = {
                "ok": False,
                "error": f"Could not read foreground window: {exc}",
                "window": {"name": "", "error": str(exc)},
                "controls": [],
            }
            if looks_like_com_error(exc):
                payload["com_error"] = True
            return payload
        try:
            focused = auto.GetFocusedControl()
        except Exception:
            focused = None
        controls: list[dict[str, Any]] = []
        walk_error = ""
        try:
            for control, depth in auto.WalkControl(window, includeTop=True, maxDepth=max_depth):
                item = self._brief(control, depth, window)
                if item is None:
                    continue
                controls.append(item)
                if len(controls) >= max_controls:
                    break
        except Exception as exc:
            walk_error = str(exc)
            if looks_like_com_error(exc):
                return self._com_failure(exc, window=self._window_meta(window))
        meta = self._window_meta(window)
        if looks_like_com_error(meta.get("error")):
            return self._com_failure(meta.get("error") or "COM error reading window", window=meta)
        if not controls and not (meta.get("name") or "").strip():
            err = walk_error or meta.get("error") or "UI Automation returned no window and 0 controls."
            payload = {
                "ok": False,
                "error": err,
                "window": meta,
                "controls": [],
            }
            if looks_like_com_error(err):
                payload["com_error"] = True
            return payload
        launch_error = detect_run_not_found(meta, controls)
        result = {
            "ok": True,
            "dry_run": False,
            "window": meta,
            "focused": self._brief(focused, 0, window) if focused is not None else None,
            "controls": controls,
            "truncated": len(controls) >= max_controls,
        }
        if walk_error:
            result["warning"] = f"WalkControl: {walk_error}"
        if launch_error:
            result["ok"] = False
            result["launch_error"] = launch_error
            result["error"] = (
                "Win+R failed: Windows cannot find that name (not on PATH). "
                "Dismiss this dialog (OK/Enter), then call launch_app with the display name, "
                "or search Start (hotkey win, type the name, enter)."
            )
        result["top_windows"] = self._top_window_summaries()
        return result

    def _com_failure(self, exc: Any, window: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "com_error": True,
            "error": (
                f"UI Automation COM error: {exc}. "
                "Desk Pilot initializes COM (STA CoInitializeEx) on the agent worker thread; "
                "if this persists, restart the app."
            ),
            "window": window or {"name": ""},
            "controls": [],
        }

    def click(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        prep = self._prepare()
        if prep:
            return prep
        auto = self.auto
        if automation_id or name:
            control = self._find_control(automation_id, name)
            if control is None:
                if x is not None and y is not None:
                    return self._click_xy(int(x), int(y))
                return {
                    "ok": False,
                    "error": "Control not found. Pass coordinates or call list_ui again.",
                    "automation_id": automation_id,
                    "name": name,
                }
            try:
                control.SetFocus()
            except Exception:
                pass
            try:
                rect = _rect_list(control.BoundingRectangle)
                control.Click()
                return {
                    "ok": True,
                    "clicked": {
                        "name": control.Name,
                        "type": _short_type(control.ControlTypeName),
                        "automation_id": control.AutomationId,
                        "rect": rect,
                    },
                }
            except Exception as exc:
                rect = _rect_list(getattr(control, "BoundingRectangle", None))
                if len(rect) == 4 and rect[2] > rect[0]:
                    cx = (rect[0] + rect[2]) // 2
                    cy = (rect[1] + rect[3]) // 2
                    fallback = self._click_xy(cx, cy)
                    fallback["note"] = f"UIA Click failed ({exc}); used coordinate fallback."
                    return fallback
                return {"ok": False, "error": f"Click failed: {exc}"}
        if x is None or y is None:
            return {"ok": False, "error": "Provide automation_id, name, or x and y."}
        return self._click_xy(int(x), int(y))

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        points: list[Any] | None = None,
    ) -> dict[str, Any]:
        from desk_pilot.desktop.drag import build_drag_path

        prep = self._prepare()
        if prep:
            return prep
        path = build_drag_path(int(x1), int(y1), int(x2), int(y2), points)
        if len(path) < 2:
            return {"ok": False, "error": "drag needs two points."}
        try:
            from pynput.mouse import Button, Controller

            mouse = Controller()
            mouse.position = path[0]
            time.sleep(0.02)
            mouse.press(Button.left)
            for x, y in path[1:]:
                mouse.position = (x, y)
                time.sleep(0.008)
            mouse.release(Button.left)
            return {
                "ok": True,
                "from": [int(x1), int(y1)],
                "to": [int(x2), int(y2)],
                "points": len(path),
                "method": "pynput",
            }
        except Exception as exc:
            return {"ok": False, "error": f"drag failed: {exc}"}

    def type_text(
        self,
        text: str,
        automation_id: str | None = None,
        name: str | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        prep = self._prepare()
        if prep:
            return prep
        auto = self.auto
        control = None
        if automation_id or name:
            control = self._find_control(automation_id, name)
            if control is None:
                return {"ok": False, "error": "Target control not found for type_text."}
        else:
            try:
                control = auto.GetFocusedControl()
            except Exception:
                control = None
        if control is not None:
            try:
                control.SetFocus()
            except Exception:
                pass
            if clear:
                try:
                    pattern = control.GetPattern(auto.PatternId.ValuePattern)
                    if pattern is not None:
                        pattern.SetValue("")
                    else:
                        control.SendKeys("{Ctrl}a{Delete}")
                except Exception:
                    self._hotkey_fallback(["ctrl", "a"])
                    self._type_fallback("")
            try:
                pattern = control.GetPattern(auto.PatternId.ValuePattern)
                if pattern is not None and not any(ch in text for ch in "\n\r"):
                    existing = ""
                    try:
                        existing = pattern.Value or ""
                    except Exception:
                        existing = ""
                    pattern.SetValue((existing if not clear else "") + text)
                    return {
                        "ok": True,
                        "typed": text,
                        "method": "ValuePattern",
                        "name": control.Name,
                        "automation_id": control.AutomationId,
                    }
            except Exception:
                pass
            try:
                control.SendKeys(_escape_sendkeys(text), interval=0.01)
                return {
                    "ok": True,
                    "typed": text,
                    "method": "SendKeys",
                    "name": getattr(control, "Name", ""),
                }
            except Exception:
                pass
        typed = self._type_fallback(text)
        typed["name"] = getattr(control, "Name", "") if control is not None else ""
        return typed

    def hotkey(self, keys: str) -> dict[str, Any]:
        prep = self._prepare()
        if prep:
            return prep
        parts = _parse_hotkey(keys)
        if not parts:
            return {"ok": False, "error": "Empty hotkey."}
        send = _to_sendkeys(parts)
        try:
            self.auto.SendKeys(send)
            return {"ok": True, "keys": "+".join(parts), "method": "SendKeys", "send": send}
        except Exception as exc:
            fallback = self._hotkey_fallback(parts)
            fallback["note"] = f"SendKeys failed ({exc}); used pynput."
            return fallback

    def screenshot_region(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
        prep = self._prepare()
        if prep:
            return prep
        import mss
        from PIL import Image

        width = max(1, min(int(width), 1920))
        height = max(1, min(int(height), 1080))
        left, top = int(x), int(y)
        region = {"left": left, "top": top, "width": width, "height": height}
        path = screenshot_dir() / f"region_{int(time.time() * 1000)}.png"
        try:
            with mss.mss() as sct:
                raw = sct.grab(region)
                image = Image.frombytes("RGB", raw.size, raw.rgb)
            if image.width > 1280 or image.height > 720:
                image.thumbnail((1280, 720))
            image.save(path)
        except Exception as exc:
            return {"ok": False, "error": f"mss capture failed: {exc}"}
        return {
            "ok": True,
            "path": str(path),
            "region": [left, top, width, height],
            "description": f"Captured {image.width}x{image.height} region at ({left},{top}). Prefer list_ui next time.",
        }

    def wait_for_window(
        self,
        title_contains: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        from desk_pilot.desktop.com import looks_like_com_error
        from desk_pilot.desktop.launch import detect_run_not_found

        prep = self._prepare()
        if prep:
            return prep
        needle = (title_contains or "").strip().lower()
        deadline = time.time() + max(0.2, float(timeout_seconds))
        last = ""
        while time.time() < deadline:
            snapshot = self._list_ui_inner(max_depth=3, max_controls=40)
            if snapshot.get("com_error") or looks_like_com_error(snapshot.get("error")):
                time.sleep(0.25)
                last = str(snapshot.get("error") or "")
                continue
            launch_error = snapshot.get("launch_error") or detect_run_not_found(
                snapshot.get("window") if isinstance(snapshot.get("window"), dict) else None,
                snapshot.get("controls") if isinstance(snapshot.get("controls"), list) else None,
            )
            window = snapshot.get("window") or {}
            last = str(window.get("name") or "")
            if launch_error:
                return {
                    "ok": False,
                    "launch_error": launch_error,
                    "window": last,
                    "error": (
                        f"Foreground is a launch-failure dialog, not {title_contains!r}. "
                        f"{launch_error}. Dismiss it, then call launch_app."
                    ),
                }
            if not needle or needle in last.lower():
                return {"ok": True, "window": last}
            time.sleep(0.25)
        return {
            "ok": False,
            "error": f"Timed out waiting for title containing {title_contains!r}. Foreground is {last!r}.",
            "window": last,
        }

    def launch_app(self, name: str) -> dict[str, Any]:
        from desk_pilot.desktop.launch import (
            app_path_candidates,
            match_start_app,
            start_apps_catalog,
            start_apps_folder,
            start_file,
            start_menu_shortcuts,
            which_candidates,
        )

        prep = self._prepare()
        if prep:
            return prep
        query = (name or "").strip()
        if not query:
            return {"ok": False, "error": "launch_app requires a name."}

        existing = self._focus_matching_window(query)
        if existing.get("ok"):
            existing["reused"] = True
            existing["method"] = "existing_window"
            existing["hint"] = "An instance was already open; focused it instead of launching another."
            return existing

        tried: list[str] = []

        for candidate in which_candidates(query):
            tried.append(f"path:{candidate}")
            if start_file(candidate):
                return {
                    "ok": True,
                    "method": "path",
                    "started": candidate,
                    "tried": tried,
                    "hint": "Wait for the window, then list_ui.",
                }
        for candidate in app_path_candidates(query):
            tried.append(f"app_paths:{candidate}")
            if start_file(candidate):
                return {
                    "ok": True,
                    "method": "app_paths",
                    "started": candidate,
                    "tried": tried,
                    "hint": "Wait for the window, then list_ui.",
                }
        for shortcut in start_menu_shortcuts(query):
            tried.append(f"start_menu:{shortcut}")
            if start_file(shortcut):
                return {
                    "ok": True,
                    "method": "start_menu",
                    "started": shortcut,
                    "tried": tried,
                    "hint": "Wait for the window, then list_ui.",
                }
        if self._start_apps_cache is None:
            self._start_apps_cache = start_apps_catalog()
        match = match_start_app(query, self._start_apps_cache)
        if match:
            tried.append(f"appsfolder:{match['appid']}")
            if start_apps_folder(match["appid"]):
                return {
                    "ok": True,
                    "method": "appsfolder",
                    "started": match["name"],
                    "appid": match["appid"],
                    "tried": tried,
                    "hint": "Wait for the window, then list_ui.",
                }
        return {
            "ok": False,
            "error": (
                f"Could not find an installed app matching {query!r}. "
                "Win+R only works for names on PATH. Dismiss any 'Windows cannot find' "
                "dialog, then search Start (hotkey win, type the name, enter)."
            ),
            "tried": tried[:16],
        }

    def list_windows(self) -> dict[str, Any]:
        prep = self._prepare()
        if prep:
            return prep
        windows = self._top_window_summaries()
        return {
            "ok": True,
            "windows": windows,
            "hint": "If the target app is listed, call focus_window instead of launch_app.",
        }

    def focus_window(
        self,
        title_contains: str | None = None,
        process_contains: str | None = None,
    ) -> dict[str, Any]:
        prep = self._prepare()
        if prep:
            return prep
        query = (title_contains or process_contains or "").strip()
        if not query:
            return {"ok": False, "error": "focus_window needs title_contains or process_contains."}
        result = self._focus_matching_window(query)
        if result.get("ok"):
            return result
        return {
            "ok": False,
            "error": f"No open window matching {query!r}.",
            "windows": self._top_window_summaries(),
        }

    def _top_window_summaries(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        try:
            for control in self._iter_top_windows():
                summary = self._window_summary(control)
                if summary:
                    items.append(summary)
                if len(items) >= 24:
                    break
        except Exception:
            return items
        return items

    def _iter_top_windows(self) -> list[Any]:
        auto = self.auto
        try:
            root = auto.GetRootControl()
        except Exception:
            return []
        found: list[Any] = []
        try:
            for control, _depth in auto.WalkControl(root, includeTop=False, maxDepth=1):
                found.append(control)
        except Exception:
            return found
        return found

    def _window_summary(self, control: Any) -> dict[str, Any] | None:
        try:
            name = (control.Name or "").strip()
        except Exception:
            return None
        if not name:
            return None
        try:
            class_name = (getattr(control, "ClassName", "") or "").strip()
        except Exception:
            class_name = ""
        if class_name.lower() in {"progman", "workerw", "shell_traywnd"}:
            return None
        process = ""
        try:
            process = _process_stem(int(control.ProcessId))
        except Exception:
            process = ""
        focused = False
        try:
            fg = self.auto.GetForegroundControl()
            focused = bool(fg is not None and self.auto.ControlsAreSame(control, fg))
        except Exception:
            focused = False
        return {
            "name": name[:120],
            "class": class_name,
            "process": process,
            "focused": focused,
        }

    def _focus_matching_window(self, query: str) -> dict[str, Any]:
        from desk_pilot.desktop.launch import is_agent_window, window_match_score

        best: tuple[int, Any, dict[str, Any]] | None = None
        for control in self._iter_top_windows():
            summary = self._window_summary(control)
            if not summary:
                continue
            if is_agent_window(summary.get("name") or "", summary.get("process") or ""):
                continue
            score = window_match_score(
                query,
                title=summary.get("name") or "",
                process=summary.get("process") or "",
            )
            if score < 55:
                continue
            if best is None or score > best[0]:
                best = (score, control, summary)
        if best is None:
            return {"ok": False, "error": f"No open window matching {query!r}."}
        _score, control, summary = best
        activated = self._activate_control(control)
        activated["window"] = summary.get("name") or ""
        activated["process"] = summary.get("process") or ""
        activated["score"] = _score
        return activated

    def _activate_control(self, control: Any) -> dict[str, Any]:
        import ctypes

        hwnd = 0
        try:
            hwnd = int(control.NativeWindowHandle or 0)
        except Exception:
            hwnd = 0
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        try:
            control.SetActive()
        except Exception:
            pass
        try:
            control.SetFocus()
        except Exception:
            pass
        try:
            name = control.Name or ""
        except Exception:
            name = ""
        return {"ok": True, "window": name, "method": "focus"}

    def _window_meta(self, window: Any) -> dict[str, Any]:
        try:
            process = ""
            try:
                process = _process_stem(int(window.ProcessId))
            except Exception:
                process = ""
            return {
                "name": window.Name or "",
                "type": _short_type(window.ControlTypeName),
                "class": getattr(window, "ClassName", "") or "",
                "automation_id": getattr(window, "AutomationId", "") or "",
                "process": process,
                "rect": _rect_list(window.BoundingRectangle),
            }
        except Exception as exc:
            return {"name": "", "error": str(exc)}

    def _brief(self, control: Any, depth: int, root: Any) -> dict[str, Any] | None:
        if control is None:
            return None
        try:
            ctype = control.ControlTypeName or ""
            name = (control.Name or "").strip()
            aid = (control.AutomationId or "").strip()
            try:
                offscreen = bool(control.IsOffscreen)
            except Exception:
                offscreen = False
            if offscreen and depth > 0:
                return None
            interesting = ctype in _INTERACTIVE or bool(name) or bool(aid)
            if depth > 0 and not interesting:
                return None
            if ctype in {"ThumbControl", "ScrollBarControl", "SeparatorControl", "ImageControl"} and not aid:
                return None
            if len(name) > 80:
                name = name[:77] + "…"
            path = self._path(control, root)
            return {
                "name": name,
                "type": _short_type(ctype),
                "automation_id": aid,
                "rect": _rect_list(control.BoundingRectangle),
                "path": path,
            }
        except Exception:
            return None

    def _path(self, control: Any, root: Any, limit: int = 4) -> str:
        parts: list[str] = []
        node = control
        for _ in range(limit):
            if node is None:
                break
            try:
                label = (node.Name or "").strip() or _short_type(node.ControlTypeName)
            except Exception:
                break
            if len(label) > 32:
                label = label[:29] + "…"
            parts.append(label)
            try:
                if root is not None and self.auto.ControlsAreSame(node, root):
                    break
            except Exception:
                pass
            try:
                node = node.GetParentControl()
            except Exception:
                break
        parts.reverse()
        return "/".join(parts[-4:])

    def _find_control(self, automation_id: str | None, name: str | None) -> Any | None:
        auto = self.auto
        try:
            window = auto.GetForegroundControl()
        except Exception:
            return None
        aid = (automation_id or "").strip()
        nam = (name or "").strip()
        try:
            for control, _depth in auto.WalkControl(window, includeTop=True, maxDepth=8):
                try:
                    if aid and (control.AutomationId or "") == aid:
                        return control
                except Exception:
                    continue
            if nam:
                nam_l = nam.lower()
                for control, _depth in auto.WalkControl(window, includeTop=True, maxDepth=8):
                    try:
                        if (control.Name or "").strip().lower() == nam_l:
                            return control
                    except Exception:
                        continue
                for control, _depth in auto.WalkControl(window, includeTop=True, maxDepth=8):
                    try:
                        if nam_l in (control.Name or "").lower():
                            return control
                    except Exception:
                        continue
        except Exception:
            return None
        return None

    def find_control_rect(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> list[int] | None:
        from desk_pilot.desktop.rects import sketchable_rect, xy_pad_rect

        prep = self._prepare()
        if prep:
            return None
        if automation_id or name:
            control = self._find_control(automation_id, name)
            if control is not None:
                box = sketchable_rect(_rect_list(getattr(control, "BoundingRectangle", None)))
                if box:
                    return box
        return xy_pad_rect(x, y, pad=12)

    def focused_window_rect(self) -> list[int] | None:
        from desk_pilot.desktop.rects import sketchable_rect

        prep = self._prepare()
        if prep:
            return None
        try:
            window = self.auto.GetForegroundControl()
        except Exception:
            return None
        if window is None:
            return None
        return sketchable_rect(_rect_list(getattr(window, "BoundingRectangle", None)))

    def window_rect_by_title(self, title: str) -> list[int] | None:
        from desk_pilot.desktop.launch import is_agent_window, window_match_score
        from desk_pilot.desktop.rects import sketchable_rect

        query = (title or "").strip()
        if not query:
            return None
        prep = self._prepare()
        if prep:
            return None
        best: tuple[int, list[int]] | None = None
        for control in self._iter_top_windows():
            summary = self._window_summary(control)
            if not summary:
                continue
            if is_agent_window(summary.get("name") or "", summary.get("process") or ""):
                continue
            score = window_match_score(
                query,
                title=summary.get("name") or "",
                process=summary.get("process") or "",
            )
            if score < 40:
                continue
            box = sketchable_rect(_rect_list(getattr(control, "BoundingRectangle", None)))
            if not box:
                continue
            if best is None or score > best[0]:
                best = (score, box)
        return None if best is None else best[1]

    def show_highlight(self, rect: list[int] | tuple[int, ...] | None) -> dict[str, Any]:
        from desk_pilot.desktop.overlay import get_overlay
        from desk_pilot.desktop.rects import SKIP_NO_RECT, rect_skip_reason, sketchable_rect

        box = sketchable_rect(rect)
        if not box:
            return {
                "ok": False,
                "skipped": True,
                "error": rect_skip_reason(rect) or SKIP_NO_RECT,
                "rect": None,
            }
        try:
            result = get_overlay().show(box)
        except Exception as exc:  # noqa: BLE001 — overlay create/blit must reach the log
            return {
                "ok": False,
                "skipped": False,
                "error": f"{type(exc).__name__}: {exc}",
                "rect": box,
            }
        if isinstance(result, dict):
            return result
        return {"ok": bool(result), "skipped": False, "error": None if result else "overlay show failed", "rect": box}

    def hide_highlight(self) -> None:
        try:
            from desk_pilot.desktop.overlay import get_overlay

            get_overlay().hide()
        except Exception:
            return

    def _click_xy(self, x: int, y: int) -> dict[str, Any]:
        try:
            self.auto.Click(x, y)
            return {"ok": True, "clicked": {"x": x, "y": y}, "method": "uiautomation.Click"}
        except Exception as exc:
            try:
                from pynput.mouse import Button, Controller

                mouse = Controller()
                mouse.position = (x, y)
                mouse.click(Button.left, 1)
                return {"ok": True, "clicked": {"x": x, "y": y}, "method": "pynput", "note": str(exc)}
            except Exception as exc2:
                return {"ok": False, "error": f"Click at {x},{y} failed: {exc2}"}

    def _type_fallback(self, text: str) -> dict[str, Any]:
        try:
            from pynput.keyboard import Controller

            Controller().type(text)
            return {"ok": True, "typed": text, "method": "pynput"}
        except Exception as exc:
            return {"ok": False, "error": f"type_text fallback failed: {exc}"}

    def _hotkey_fallback(self, parts: list[str]) -> dict[str, Any]:
        try:
            from pynput.keyboard import Controller, Key

            mapping = {
                "ctrl": Key.ctrl,
                "alt": Key.alt,
                "shift": Key.shift,
                "win": Key.cmd,
                "enter": Key.enter,
                "tab": Key.tab,
                "escape": Key.esc,
                "esc": Key.esc,
                "backspace": Key.backspace,
                "delete": Key.delete,
                "space": Key.space,
                "up": Key.up,
                "down": Key.down,
                "left": Key.left,
                "right": Key.right,
                "home": Key.home,
                "end": Key.end,
                "pageup": Key.page_up,
                "pagedown": Key.page_down,
                **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)},
            }
            kb = Controller()
            resolved = []
            for part in parts:
                resolved.append(mapping.get(part, part))
            for key in resolved:
                kb.press(key)
            for key in reversed(resolved):
                kb.release(key)
            return {"ok": True, "keys": "+".join(parts), "method": "pynput"}
        except Exception as exc:
                return {"ok": False, "error": f"hotkey fallback failed: {exc}"}


def _process_stem(pid: int) -> str:
    if not pid:
        return ""
    import ctypes
    from ctypes import wintypes
    from pathlib import Path

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(32768)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).stem.lower()
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(handle)
    return ""
