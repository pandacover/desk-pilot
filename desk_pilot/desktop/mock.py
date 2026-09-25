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
        self._missing_name = "app"
        self._open_apps: dict[str, dict[str, str]] = {}
        self.highlights: list[list[int]] = []
        self.highlight_visible: list[int] | None = None
        self.fail_highlight = False
        self.fail_highlight_error = "UpdateLayeredWindow failed (GetLastError=87)"
        self.drags: list[dict[str, Any]] = []
        self.clipboard_image: str | None = None
        self.steal_focus_after_action = False
        self.address_value = ""
        self.stale_nav = False
        self.hide_address_bar = False
        self.files_catalog: list[dict[str, Any]] = [
            {
                "path": r"C:\Program Files (x86)\Steam\steamapps\common\Brawlhalla\Brawlhalla.exe",
                "size": 18432000,
                "mtime": "2024-06-01T12:00:00Z",
            },
            {
                "path": r"C:\Users\mock\Desktop\Brawlhalla.lnk",
                "size": 2048,
                "mtime": "2024-06-01T12:00:00Z",
            },
            {
                "path": r"C:\Users\mock\Downloads\readme.txt",
                "size": 120,
                "mtime": "2024-06-02T12:00:00Z",
            },
        ]

    def reset(self) -> None:
        self.__init__()

    def list_ui(self, max_depth: int = 5, max_controls: int = 70) -> dict[str, Any]:
        from desk_pilot.desktop.launch import detect_run_not_found

        window, focused, controls = self._scene_tree()
        visible = controls[: max(1, max_controls)]
        launch_error = detect_run_not_found(window, visible)
        payload: dict[str, Any] = {
            "ok": not bool(launch_error),
            "dry_run": True,
            "window": window,
            "focused": focused,
            "controls": visible,
            "hint": "Dry-run backend. Prefer focus_window when the app is already in top_windows.",
            "top_windows": self._top_window_summaries(),
        }
        if launch_error:
            payload["launch_error"] = launch_error
            payload["error"] = f"Win+R failed: {launch_error}"
        return payload

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
            elif n in {"ok", "close"} and self.scene == "run_error":
                self.scene = "desktop"
                self.window_title = "Desktop"
            elif n in {"notepad"} or aid == "notepad_tile":
                existing = self._open_apps.get("notepad")
                if existing:
                    self._focus_app(existing)
                else:
                    self._open_notepad()
            return self._maybe_steal_focus(
                {"ok": True, "dry_run": True, "clicked": target, "window": self.window_title}
            )
        if x is not None and y is not None:
            result = {"ok": True, "dry_run": True, "clicked": {"x": x, "y": y}, "note": "coordinate click (simulated)"}
            return self._maybe_steal_focus(result)
        return {"ok": False, "dry_run": True, "error": "No matching control. Use list_ui names/ids or coordinates."}

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        points: list[Any] | None = None,
    ) -> dict[str, Any]:
        from desk_pilot.desktop.drag import build_drag_path

        path = build_drag_path(int(x1), int(y1), int(x2), int(y2), points)
        stroke = {
            "from": [int(x1), int(y1)],
            "to": [int(x2), int(y2)],
            "points": [list(p) for p in path],
        }
        self.drags.append(stroke)
        self.actions.append(f"drag {x1},{y1} -> {x2},{y2}")
        result = {
            "ok": True,
            "dry_run": True,
            "from": stroke["from"],
            "to": stroke["to"],
            "points": len(path),
            "method": "mock",
            "window": self.window_title,
        }
        return self._maybe_steal_focus(result)

    def set_clipboard_image(self, path: str) -> dict[str, Any]:
        self.clipboard_image = path
        self.actions.append(f"clipboard {path}")
        return {"ok": True, "dry_run": True, "path": path}

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
            value = self.run_text
        elif self.scene == "tldraw":
            self.address_value = "" if clear else self.address_value
            self.address_value += text
            target = "Address"
            value = self.address_value
            if "tldraw" in text.lower():
                self._open_tldraw()
        else:
            self.edit_text = "" if clear else self.edit_text
            self.edit_text += text
            target = "Notepad.Edit" if self.scene == "notepad" else "focused"
            value = self.edit_text
        return self._maybe_steal_focus(
            {
                "ok": True,
                "dry_run": True,
                "typed": text,
                "clear": clear,
                "target": target,
                "value": value,
            }
        )

    def hotkey(self, keys: str) -> dict[str, Any]:
        chord = (keys or "").strip().lower().replace(" ", "")
        self.actions.append(f"hotkey {chord}")
        if chord in {"win+r", "meta+r", "cmd+r"}:
            self.scene = "run"
            self.run_text = ""
            self.window_title = "Run"
            return {"ok": True, "dry_run": True, "keys": chord, "window": "Run"}
        if chord in {"win", "meta", "cmd"}:
            self.scene = "start"
            self.edit_text = ""
            self.window_title = "Start"
            return {"ok": True, "dry_run": True, "keys": chord, "window": "Start"}
        if chord in {"ctrl+l"}:
            if self.scene == "tldraw":
                return {
                    "ok": True,
                    "dry_run": True,
                    "keys": chord,
                    "window": self.window_title,
                    "note": "Address bar focused",
                }
        if chord in {"enter", "return"}:
            if self.scene == "run_error":
                self.scene = "desktop"
                self.window_title = "Desktop"
                return {"ok": True, "dry_run": True, "keys": chord, "window": "Desktop", "note": "Dismissed launch-error dialog."}
            if self.scene == "run" and "tldraw" in self.run_text.lower():
                self._open_tldraw()
                return self._maybe_steal_focus(
                    {"ok": True, "dry_run": True, "keys": chord, "window": self.window_title}
                )
            if self.scene == "run" and "notepad" in self.run_text.lower():
                self._open_notepad()
                return {"ok": True, "dry_run": True, "keys": chord, "window": "Untitled - Notepad"}
            if self.scene == "run":
                self._open_run_not_found(self.run_text)
                return {
                    "ok": False,
                    "dry_run": True,
                    "keys": chord,
                    "launch_error": True,
                    "window": self.window_title,
                    "error": f"Windows cannot find '{self._missing_name}'. Make sure you typed the name correctly, and then try again.",
                }
            if self.scene == "start" and "notepad" in self.edit_text.lower():
                self._open_notepad()
                return {"ok": True, "dry_run": True, "keys": chord, "window": "Untitled - Notepad"}
            if self.scene == "tldraw":
                self._apply_browser_navigation(self.address_value)
                return self._maybe_steal_focus(
                    {"ok": True, "dry_run": True, "keys": chord, "window": self.window_title}
                )
        if chord in {"alt+f4", "alt+f4"}:
            self._close_current_app()
            self.scene = "desktop"
            self.window_title = "Desktop"
        if chord in {"ctrl+a"}:
            pass
        if chord in {"ctrl+s"}:
            return {"ok": True, "dry_run": True, "keys": chord, "note": "Save simulated (no file dialog)."}
        if chord in {"ctrl+v"} and self.clipboard_image:
            self.actions.append(f"paste {self.clipboard_image}")
            return {
                "ok": True,
                "dry_run": True,
                "keys": chord,
                "window": self.window_title,
                "pasted": self.clipboard_image,
            }
        return self._maybe_steal_focus({"ok": True, "dry_run": True, "keys": chord, "window": self.window_title})

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
        from desk_pilot.desktop.launch import detect_run_not_found

        window, _focused, controls = self._scene_tree()
        launch_error = detect_run_not_found(window, controls)
        if launch_error:
            return {
                "ok": False,
                "dry_run": True,
                "launch_error": launch_error,
                "window": self.window_title,
                "error": (
                    f"Foreground is a launch-failure dialog, not {title_contains!r}. "
                    f"{launch_error}. Dismiss it, then call launch_app."
                ),
            }
        needle = (title_contains or "").lower()
        if not needle or needle in self.window_title.lower():
            return {"ok": True, "dry_run": True, "window": self.window_title}
        deadline = time.time() + max(0.1, float(timeout_seconds))
        while time.time() < deadline:
            if needle in self.window_title.lower() and self.scene != "run_error":
                return {"ok": True, "dry_run": True, "window": self.window_title}
            time.sleep(0.05)
        return {
            "ok": False,
            "dry_run": True,
            "error": f"Timed out waiting for title containing {title_contains!r}. Foreground is {self.window_title!r}.",
        }

    def launch_app(self, name: str) -> dict[str, Any]:
        query = (name or "").strip()
        self.actions.append(f"launch_app {query!r}")
        existing = self._match_open_app(query)
        if existing:
            self._focus_app(existing)
            return {
                "ok": True,
                "dry_run": True,
                "reused": True,
                "method": "existing_window",
                "window": self.window_title,
                "hint": "An instance was already open; focused it instead of launching another.",
            }
        key = query.lower().replace(".exe", "")
        if "notepad" in key:
            self._open_notepad()
            return {
                "ok": True,
                "dry_run": True,
                "method": "mock_catalog",
                "started": "notepad",
                "window": self.window_title,
            }
        if any(token in key for token in ("tldraw", "chrome", "edge", "firefox", "msedge")):
            title = "tldraw" if "tldraw" in key else query
            self._open_tldraw(title=title)
            return {
                "ok": True,
                "dry_run": True,
                "method": "mock_catalog",
                "started": "tldraw" if "tldraw" in key else key,
                "window": self.window_title,
            }
        self._open_run_not_found(query)
        return {
            "ok": False,
            "dry_run": True,
            "launch_error": True,
            "error": (
                f"Windows cannot find '{query}'. Make sure you typed the name correctly, "
                "and then try again."
            ),
            "hint": "Dismiss the dialog, then try Start search or another name. Dry-run catalog includes notepad.",
        }

    def list_windows(self) -> dict[str, Any]:
        windows = self._top_window_summaries()
        return {
            "ok": True,
            "dry_run": True,
            "windows": windows,
            "hint": "If the target app is listed, call focus_window instead of launch_app.",
        }

    def focus_window(
        self,
        title_contains: str | None = None,
        process_contains: str | None = None,
    ) -> dict[str, Any]:
        query = (title_contains or process_contains or "").strip()
        self.actions.append(f"focus_window {query!r}")
        if not query:
            return {"ok": False, "dry_run": True, "error": "focus_window needs title_contains or process_contains."}
        existing = self._match_open_app(query)
        if not existing:
            return {
                "ok": False,
                "dry_run": True,
                "error": f"No open window matching {query!r}.",
                "windows": self._top_window_summaries(),
            }
        self._focus_app(existing)
        return {"ok": True, "dry_run": True, "window": self.window_title, "method": "focus"}

    def find_files(
        self,
        name: str | None = None,
        glob: str | None = None,
        max_results: int = 20,
    ) -> dict[str, Any]:
        from fnmatch import fnmatch
        from pathlib import Path

        query = (name or "").strip() or None
        pattern = (glob or "").strip() or None
        if query and any(ch in query for ch in "*?["):
            pattern = query
            query = None
        if not query and not pattern:
            return {
                "ok": False,
                "dry_run": True,
                "error": "find_files needs name or glob (e.g. brawlhalla.exe or *.exe).",
                "results": [],
            }
        try:
            limit = max(1, min(int(max_results), 50))
        except (TypeError, ValueError):
            limit = 20
        hits: list[dict[str, Any]] = []
        for item in self.files_catalog:
            path = str(item.get("path") or "")
            base = Path(path.replace("\\", "/")).name
            if query and query.lower() not in base.lower() and query.lower() not in path.lower():
                continue
            if pattern and not (fnmatch(base, pattern) or fnmatch(path, pattern)):
                continue
            hits.append(dict(item))
            if len(hits) >= limit:
                break
        return {
            "ok": True,
            "dry_run": True,
            "results": hits,
            "count": len(hits),
            "name": query,
            "glob": pattern,
            "hint": "Use these full paths. Do not Win+S the filename into web search.",
        }

    def find_control_rect(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> list[int] | None:
        from desk_pilot.desktop.rects import sketchable_rect, xy_pad_rect

        target = self._find(automation_id, name)
        if target:
            box = sketchable_rect(target.get("rect"))
            if box:
                return box
        return xy_pad_rect(x, y, pad=10)

    def focused_window_rect(self) -> list[int] | None:
        from desk_pilot.desktop.rects import sketchable_rect

        window, focused, controls = self._scene_tree()
        title = (window.get("name") or self.window_title or "").strip()
        for item in controls:
            if (item.get("type") or "") not in {"Window", "Pane"}:
                continue
            if title and (item.get("name") or "") != title:
                continue
            box = sketchable_rect(item.get("rect"))
            if box:
                return box
        for item in controls:
            if (item.get("type") or "") in {"Window", "Pane"}:
                box = sketchable_rect(item.get("rect"))
                if box:
                    return box
        return sketchable_rect((focused or {}).get("rect"))

    def window_rect_by_title(self, title: str) -> list[int] | None:
        from desk_pilot.desktop.launch import window_match_score
        from desk_pilot.desktop.rects import sketchable_rect

        query = (title or "").strip()
        if not query:
            return None
        best: tuple[int, list[int]] | None = None
        for app in self._open_apps.values():
            score = window_match_score(
                query,
                title=app.get("name") or "",
                process=app.get("process") or "",
            )
            if score < 40:
                continue
            box = sketchable_rect(app.get("rect"))
            if not box:
                continue
            if best is None or score > best[0]:
                best = (score, box)
        if best:
            return best[1]
        _window, _focused, controls = self._scene_tree()
        for item in controls:
            score = window_match_score(query, title=item.get("name") or "")
            if score < 40:
                continue
            box = sketchable_rect(item.get("rect"))
            if not box:
                continue
            if best is None or score > best[0]:
                best = (score, box)
        return None if best is None else best[1]

    def show_highlight(self, rect: list[int] | tuple[int, ...] | None) -> dict[str, Any]:
        from desk_pilot.desktop.rects import SKIP_NO_RECT, rect_skip_reason, sketchable_rect

        box = sketchable_rect(rect)
        if not box:
            return {
                "ok": False,
                "skipped": True,
                "error": rect_skip_reason(rect) or SKIP_NO_RECT,
                "rect": None,
            }
        if self.fail_highlight:
            return {
                "ok": False,
                "skipped": False,
                "error": self.fail_highlight_error,
                "rect": box,
            }
        self.highlights.append(box)
        self.highlight_visible = box
        return {"ok": True, "skipped": False, "dry_run": True, "error": None, "rect": box}

    def hide_highlight(self) -> None:
        self.highlight_visible = None

    def _maybe_steal_focus(self, result: dict[str, Any]) -> dict[str, Any]:
        if not self.steal_focus_after_action:
            return result
        if self.scene == "desk_pilot":
            return result
        self._focus_before_steal = {
            "scene": self.scene,
            "title": self.window_title,
        }
        self.scene = "desk_pilot"
        self.window_title = "Desk Pilot"
        return result

    def _open_notepad(self) -> None:
        self.scene = "notepad"
        self.edit_text = ""
        self.window_title = "Untitled - Notepad"
        self._upsert_app("notepad", self.window_title, "notepad", rect=[0, 0, 810, 570])

    def _open_tldraw(self, title: str = "tldraw") -> None:
        label = "tldraw" if "tldraw" in (title or "").lower() else (title or "tldraw")
        self.scene = "tldraw"
        self.window_title = f"{label} - Helium"
        self.address_value = getattr(self, "address_value", "") or "https://www.tldraw.com"
        self._upsert_app("helium", self.window_title, "tldraw", rect=[80, 40, 1280, 800])

    def _apply_browser_navigation(self, typed: str) -> None:
        url = (typed or "").strip()
        if not url or self.stale_nav:
            return
        lower = url.lower()
        if "tldraw" in lower:
            self._open_tldraw()
            return
        host = url
        for prefix in ("https://", "http://"):
            if host.lower().startswith(prefix):
                host = host[len(prefix) :]
                break
        host = host.split("/")[0] or host
        if host.startswith("www."):
            host = host[4:]
        label = host or url
        self.window_title = f"{label} - Helium"
        self.address_value = url
        self._upsert_app("helium", self.window_title, "tldraw", rect=[80, 40, 1280, 800])

    def _upsert_app(
        self,
        process: str,
        title: str,
        scene: str,
        rect: list[int] | None = None,
    ) -> None:
        previous = self._open_apps.get(process) or {}
        self._open_apps[process] = {
            "name": title,
            "process": process,
            "scene": scene,
            "rect": list(rect) if rect else list(previous.get("rect") or [80, 40, 1200, 800]),
        }

    def _close_current_app(self) -> None:
        if self.scene == "notepad":
            self._open_apps.pop("notepad", None)
        if self.scene == "tldraw":
            self._open_apps.pop("helium", None)

    def _match_open_app(self, query: str) -> dict[str, str] | None:
        from desk_pilot.desktop.launch import window_match_score

        best: tuple[int, dict[str, str]] | None = None
        for app in self._open_apps.values():
            score = window_match_score(query, title=app.get("name") or "", process=app.get("process") or "")
            if score < 55:
                continue
            if best is None or score > best[0]:
                best = (score, app)
        return None if best is None else best[1]

    def _focus_app(self, app: dict[str, str]) -> None:
        self.scene = app.get("scene") or "desktop"
        self.window_title = app.get("name") or self.window_title

    def _top_window_summaries(self) -> list[dict[str, Any]]:
        process = {
            "notepad": "notepad",
            "tldraw": "helium",
            "desk_pilot": "python",
        }.get(self.scene, "explorer")
        items: list[dict[str, Any]] = [
            {
                "name": self.window_title,
                "process": process,
                "focused": True,
            }
        ]
        seen = {self.window_title.lower()}
        for app in self._open_apps.values():
            if (app.get("name") or "").lower() in seen:
                continue
            items.append({"name": app.get("name") or "", "process": app.get("process") or "", "focused": False})
        return items

    def _open_run_not_found(self, typed: str) -> None:
        self._missing_name = (typed or "app").strip() or "app"
        self.scene = "run_error"
        self.window_title = self._missing_name

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
        if self.scene == "tldraw":
            title = self.window_title
            window = {
                "name": title,
                "type": "Window",
                "class": "Chrome_WidgetWin_1",
                "process": "helium",
                "rect": [80, 40, 1280, 800],
            }
            address = _ctrl(
                name="Address and search bar",
                ctype="Edit",
                aid="urlbar",
                rect=[140, 48, 720, 76],
                path=f"{title}/Toolbar/Address",
            )
            address = {**address, "value": self.address_value}
            document = _ctrl(
                name="Document",
                ctype="Document",
                aid="Chrome_RenderWidgetHostHWND",
                rect=[80, 88, 1280, 800],
                path=f"{title}/Document",
            )
            if getattr(self, "hide_address_bar", False):
                focused = document
                chrome = [
                    _ctrl(name=title, ctype="Window", aid="Browser", rect=[80, 40, 1280, 800], path=title),
                    _ctrl(name="Back", ctype="Button", aid="back", rect=[90, 48, 118, 76], path=f"{title}/Toolbar/Back"),
                    _ctrl(name="Forward", ctype="Button", aid="fwd", rect=[118, 48, 146, 76], path=f"{title}/Toolbar/Forward"),
                    _ctrl(name="Reload", ctype="Button", aid="reload", rect=[146, 48, 174, 76], path=f"{title}/Toolbar/Reload"),
                    _ctrl(name="tldraw", ctype="TabItem", aid="tab", rect=[180, 12, 280, 40], path=f"{title}/Tab"),
                    document,
                    _ctrl(name="Close", ctype="Button", aid="Close", rect=[1248, 44, 1272, 68], path=f"{title}/TitleBar/Close"),
                ]
                return window, focused, chrome
            focused = address
            # Chrome chrome only — the tldraw canvas is not in UIA (live ~10 controls).
            controls = [
                _ctrl(name=title, ctype="Window", aid="Browser", rect=[80, 40, 1280, 800], path=title),
                _ctrl(name="Back", ctype="Button", aid="back", rect=[90, 48, 118, 76], path=f"{title}/Toolbar/Back"),
                _ctrl(name="Forward", ctype="Button", aid="fwd", rect=[118, 48, 146, 76], path=f"{title}/Toolbar/Forward"),
                _ctrl(name="Reload", ctype="Button", aid="reload", rect=[146, 48, 174, 76], path=f"{title}/Toolbar/Reload"),
                focused,
                _ctrl(name="tldraw", ctype="TabItem", aid="tab", rect=[180, 12, 280, 40], path=f"{title}/Tab"),
                document,
                _ctrl(name="Close", ctype="Button", aid="Close", rect=[1248, 44, 1272, 68], path=f"{title}/TitleBar/Close"),
            ]
            return window, focused, controls
        if self.scene == "desk_pilot":
            window = {
                "name": "Desk Pilot",
                "type": "Window",
                "class": "TkTopLevel",
                "process": "python",
                "rect": [40, 40, 720, 640],
            }
            focused = _ctrl(
                name="Goal",
                ctype="Edit",
                aid="goal",
                rect=[60, 120, 680, 160],
                path="Desk Pilot/Goal",
            )
            controls = [
                _ctrl(name="Desk Pilot", ctype="Window", aid="DeskPilot", rect=[40, 40, 720, 640], path="Desk Pilot"),
                focused,
                _ctrl(name="Run", ctype="Button", aid="run", rect=[60, 180, 140, 220], path="Desk Pilot/Run"),
                _ctrl(name="STOP", ctype="Button", aid="stop", rect=[160, 180, 260, 220], path="Desk Pilot/STOP"),
            ]
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
        if self.scene == "run_error":
            missing = getattr(self, "_missing_name", "app")
            title = missing
            window = {"name": title, "type": "Window", "class": "#32770"}
            message = (
                f"Windows cannot find '{missing}'. Make sure you typed the name correctly, "
                "and then try again."
            )
            focused = _ctrl(name="OK", ctype="Button", aid="2", rect=[180, 110, 250, 138], path=f"{title}/OK")
            controls = [
                _ctrl(name=title, ctype="Window", aid="Error", rect=[40, 40, 420, 180], path=title),
                _ctrl(name=message, ctype="Text", aid="65535", rect=[50, 60, 400, 100], path=f"{title}/Message"),
                focused,
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
