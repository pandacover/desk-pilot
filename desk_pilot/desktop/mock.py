"""Dry-run desktop: a tiny fake Windows session so Linux CI can exercise the loop."""

from __future__ import annotations

import time
from typing import Any

from desk_pilot.app.config import screenshot_dir
from desk_pilot.desktop.base import DesktopBackend


def _ctrl(
    *,
    name: str,
    ctype: str,
    aid: str = "",
    rect: list[int],
    path: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "type": ctype,
        "automation_id": aid,
        "rect": rect,
        "path": path,
    }


class MockDesktop(DesktopBackend):
    """In-memory Notepad / Run-dialog scene. Actions are logged, never sent to OS input."""

    dry_run = True

    def __init__(self) -> None:
        self.scene = "desktop"
        self.edit_text = ""
        self.run_text = ""
        self.actions: list[str] = []
        self.window_title = "Desktop"

    def reset(self) -> None:
        self.__init__()

    def list_ui(self, max_depth: int = 5, max_controls: int = 70) -> dict[str, Any]:
        window, focused, controls = self._scene_tree()
        return {
            "dry_run": True,
            "window": window,
            "focused": focused,
            "controls": controls[: max(1, max_controls)],
            "hint": "Dry-run backend. No real mouse/keyboard. Win+R then notepad+Enter opens fake Notepad.",
        }

    def click(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        target = self._find(automation_id, name)
        label = (name or automation_id or f"{x},{y}").strip()
        self.actions.append(f"click {label}")
        if target:
            aid = (target.get("automation_id") or "").lower()
            n = (target.get("name") or "").lower()
            if aid in {"start", "startbutton"} or n in {"start", "start menu"}:
                self.scene = "start"
                self.window_title = "Start"
            elif n in {"notepad"} or aid == "notepad_tile":
                self._open_notepad()
            return {"ok": True, "dry_run": True, "clicked": target, "window": self.window_title}
        if x is not None and y is not None:
            return {"ok": True, "dry_run": True, "clicked": {"x": x, "y": y}, "note": "coordinate click (simulated)"}
        return {"ok": False, "dry_run": True, "error": "No matching control. Use list_ui names/ids or coordinates."}

    def type_text(
        self,
        text: str,
        automation_id: str | None = None,
        name: str | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        self.actions.append(f"type {text!r}")
        if self.scene == "run":
            self.run_text = "" if clear else self.run_text
            self.run_text += text
            target = "Run.Open"
        else:
            self.edit_text = "" if clear else self.edit_text
            self.edit_text += text
            target = "Notepad.Edit" if self.scene == "notepad" else "focused"
        return {
            "ok": True,
            "dry_run": True,
            "typed": text,
            "clear": clear,
            "target": target,
            "value": self.run_text if self.scene == "run" else self.edit_text,
        }

    def hotkey(self, keys: str) -> dict[str, Any]:
        chord = (keys or "").strip().lower().replace(" ", "")
        self.actions.append(f"hotkey {chord}")
        if chord in {"win+r", "meta+r", "cmd+r"}:
            self.scene = "run"
            self.run_text = ""
            self.window_title = "Run"
            return {"ok": True, "dry_run": True, "keys": chord, "window": "Run"}
        if chord in {"enter", "return"}:
            if self.scene == "run" and "notepad" in self.run_text.lower():
                self._open_notepad()
                return {"ok": True, "dry_run": True, "keys": chord, "window": "Untitled - Notepad"}
            if self.scene == "start" and "notepad" in self.edit_text.lower():
                self._open_notepad()
                return {"ok": True, "dry_run": True, "keys": chord, "window": "Untitled - Notepad"}
        if chord in {"alt+f4", "alt+f4"}:
            self.scene = "desktop"
            self.window_title = "Desktop"
        if chord in {"ctrl+a"}:
            pass
        if chord in {"ctrl+s"}:
            return {"ok": True, "dry_run": True, "keys": chord, "note": "Save simulated (no file dialog)."}
        return {"ok": True, "dry_run": True, "keys": chord, "window": self.window_title}

    def screenshot_region(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
        from PIL import Image, ImageDraw

        width = max(1, min(int(width), 1920))
        height = max(1, min(int(height), 1080))
        image = Image.new("RGB", (width, height), (32, 36, 44))
        draw = ImageDraw.Draw(image)
        draw.text((8, 8), f"dry-run {self.window_title}", fill=(220, 220, 220))
        path = screenshot_dir() / f"dryrun_{int(time.time() * 1000)}.png"
        image.save(path)
        self.actions.append(f"screenshot {x},{y} {width}x{height}")
        return {
            "ok": True,
            "dry_run": True,
            "path": str(path),
            "region": [x, y, width, height],
            "description": f"Placeholder region capture of '{self.window_title}' ({width}x{height}).",
        }

    def wait_for_window(
        self,
        title_contains: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        needle = (title_contains or "").lower()
        if not needle or needle in self.window_title.lower():
            return {"ok": True, "dry_run": True, "window": self.window_title}
        deadline = time.time() + max(0.1, float(timeout_seconds))
        while time.time() < deadline:
            if needle in self.window_title.lower():
                return {"ok": True, "dry_run": True, "window": self.window_title}
            time.sleep(0.05)
        return {
            "ok": False,
            "dry_run": True,
            "error": f"Timed out waiting for title containing {title_contains!r}. Foreground is {self.window_title!r}.",
        }

    def _open_notepad(self) -> None:
        self.scene = "notepad"
        self.edit_text = ""
        self.window_title = "Untitled - Notepad"

    def _find(self, automation_id: str | None, name: str | None) -> dict[str, Any] | None:
        _, _, controls = self._scene_tree()
        aid = (automation_id or "").strip().lower()
        nam = (name or "").strip().lower()
        for item in controls:
            if aid and (item.get("automation_id") or "").lower() == aid:
                return item
        for item in controls:
            if nam and nam in (item.get("name") or "").lower():
                return item
        return None

    def _scene_tree(self) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        if self.scene == "run":
            window = {"name": "Run", "type": "Window", "class": "#32770"}
            focused = _ctrl(
                name="Open",
                ctype="Edit",
                aid="12298",
                rect=[20, 80, 360, 104],
                path="Run/Open",
            )
            controls = [
                _ctrl(name="Run", ctype="Window", aid="Run", rect=[10, 10, 420, 180], path="Run"),
                focused,
                _ctrl(name="OK", ctype="Button", aid="1", rect=[180, 130, 250, 158], path="Run/OK"),
                _ctrl(name="Cancel", ctype="Button", aid="2", rect=[260, 130, 340, 158], path="Run/Cancel"),
            ]
            return window, focused, controls
        if self.scene == "notepad":
            window = {"name": "Untitled - Notepad", "type": "Window", "class": "Notepad"}
            focused = _ctrl(
                name="Text Editor",
                ctype="Edit",
                aid="15",
                rect=[8, 80, 800, 560],
                path="Untitled - Notepad/Edit",
            )
            controls = [
                _ctrl(name="Untitled - Notepad", ctype="Window", aid="Notepad", rect=[0, 0, 810, 570], path="Untitled - Notepad"),
                _ctrl(name="File", ctype="MenuItem", aid="File", rect=[8, 32, 48, 56], path="Untitled - Notepad/MenuBar/File"),
                _ctrl(name="Edit", ctype="MenuItem", aid="EditMenu", rect=[48, 32, 88, 56], path="Untitled - Notepad/MenuBar/Edit"),
                focused,
                _ctrl(name="Close", ctype="Button", aid="Close", rect=[770, 8, 800, 32], path="Untitled - Notepad/TitleBar/Close"),
            ]
            focused = {**focused, "value": self.edit_text}
            return window, focused, controls
        if self.scene == "start":
            window = {"name": "Start", "type": "Window", "class": "Windows.UI.Core.CoreWindow"}
            focused = _ctrl(name="Search", ctype="Edit", aid="SearchBox", rect=[20, 20, 360, 52], path="Start/Search")
            controls = [
                focused,
                _ctrl(name="Notepad", ctype="ListItem", aid="notepad_tile", rect=[20, 70, 160, 110], path="Start/Notepad"),
                _ctrl(name="Settings", ctype="ListItem", aid="settings_tile", rect=[20, 120, 160, 160], path="Start/Settings"),
            ]
            return window, focused, controls
        window = {"name": "Desktop", "type": "Pane", "class": "#32769"}
        focused = _ctrl(name="Desktop", ctype="Pane", aid="Desktop", rect=[0, 0, 1920, 1080], path="Desktop")
        controls = [
            focused,
            _ctrl(name="Start", ctype="Button", aid="StartButton", rect=[0, 1040, 48, 1080], path="Desktop/Taskbar/Start"),
            _ctrl(name="Search", ctype="Button", aid="SearchButton", rect=[48, 1040, 96, 1080], path="Desktop/Taskbar/Search"),
            _ctrl(name="Taskbar", ctype="Pane", aid="Taskbar", rect=[0, 1040, 1920, 1080], path="Desktop/Taskbar"),
        ]
        return window, focused, controls
