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
            out.append("{{}")
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
        auto.uiautomation.SetGlobalSearchTimeout(3)

    def list_ui(self, max_depth: int = 5, max_controls: int = 70) -> dict[str, Any]:
        auto = self.auto
        max_depth = max(1, min(int(max_depth), 8))
        max_controls = max(8, min(int(max_controls), 120))
        try:
            window = auto.GetForegroundControl()
        except Exception as exc:
            return {"ok": False, "error": f"Could not read foreground window: {exc}"}
        try:
            focused = auto.GetFocusedControl()
        except Exception:
            focused = None
        controls: list[dict[str, Any]] = []
        try:
            for control, depth in auto.WalkControl(window, includeTop=True, maxDepth=max_depth):
                item = self._brief(control, depth, window)
                if item is None:
                    continue
                controls.append(item)
                if len(controls) >= max_controls:
                    break
        except Exception as exc:
            return {
                "ok": False,
                "error": f"WalkControl failed: {exc}",
                "window": self._window_meta(window),
            }
        return {
            "ok": True,
            "dry_run": False,
            "window": self._window_meta(window),
            "focused": self._brief(focused, 0, window) if focused is not None else None,
            "controls": controls,
            "truncated": len(controls) >= max_controls,
        }

    def click(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
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
                control.Click()
                rect = _rect_list(control.BoundingRectangle)
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

    def type_text(
        self,
        text: str,
        automation_id: str | None = None,
        name: str | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
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
        needle = (title_contains or "").strip().lower()
        deadline = time.time() + max(0.2, float(timeout_seconds))
        last = ""
        while time.time() < deadline:
            try:
                window = self.auto.GetForegroundControl()
                last = window.Name or ""
            except Exception as exc:
                last = f"<error {exc}>"
                time.sleep(0.25)
                continue
            if not needle or needle in last.lower():
                return {"ok": True, "window": last}
            time.sleep(0.25)
        return {
            "ok": False,
            "error": f"Timed out waiting for title containing {title_contains!r}. Foreground is {last!r}.",
            "window": last,
        }

    def _window_meta(self, window: Any) -> dict[str, Any]:
        try:
            return {
                "name": window.Name or "",
                "type": _short_type(window.ControlTypeName),
                "class": getattr(window, "ClassName", "") or "",
                "automation_id": getattr(window, "AutomationId", "") or "",
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
