from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DesktopBackend(ABC):
    """UIA-first desktop control. Mock impl is used off Windows."""

    dry_run: bool = False

    def find_control_rect(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> list[int] | None:
        """UIA bounding rect for a control, without clicking or typing."""
        return None

    def focused_window_rect(self) -> list[int] | None:
        """Bounding rect of the focused top-level window, if known."""
        return None

    def window_rect_by_title(self, title: str) -> list[int] | None:
        """Bounding rect of an open window whose title matches ``title``."""
        return None

    def show_highlight(self, rect: list[int] | tuple[int, ...] | None) -> dict[str, Any]:
        """Persistent sketch overlay for guide mode. Returns {ok, skipped, error, rect}."""
        from desk_pilot.desktop.rects import SKIP_NO_RECT, rect_skip_reason, sketchable_rect

        box = sketchable_rect(rect)
        if not box:
            return {
                "ok": False,
                "skipped": True,
                "error": rect_skip_reason(rect) or SKIP_NO_RECT,
                "rect": None,
            }
        return {"ok": False, "skipped": True, "error": "overlay only on Windows", "rect": box}

    def hide_highlight(self) -> None:
        return

    @abstractmethod
    def list_ui(self, max_depth: int = 5, max_controls: int = 70) -> dict[str, Any]:
        """Compact UIA tree for the focused window."""

    @abstractmethod
    def click(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        """Click a control by automation id, name, or screen coordinates."""

    @abstractmethod
    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        points: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Mouse-down at (x1,y1), move, mouse-up at (x2,y2). Optional polyline points."""

    @abstractmethod
    def type_text(
        self,
        text: str,
        automation_id: str | None = None,
        name: str | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        """Type into the focused or targeted control."""

    @abstractmethod
    def hotkey(self, keys: str) -> dict[str, Any]:
        """Send a chord such as ctrl+s or win+r."""

    @abstractmethod
    def screenshot_region(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
        """Capture a region with mss. Use only when UIA cannot find a control."""

    @abstractmethod
    def wait_for_window(
        self,
        title_contains: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        """Wait until the foreground window title matches."""

    @abstractmethod
    def list_windows(self) -> dict[str, Any]:
        """Top-level windows (title/process), including unfocused ones."""

    @abstractmethod
    def focus_window(
        self,
        title_contains: str | None = None,
        process_contains: str | None = None,
    ) -> dict[str, Any]:
        """Activate an already-open top-level window."""

    @abstractmethod
    def launch_app(self, name: str) -> dict[str, Any]:
        """Start an installed app, or focus it if it is already open."""

    def find_address_element(self) -> Any | None:
        """Live UIA handle (or mock row) for the Chromium address bar, if present."""
        from desk_pilot.agent.nav import address_control_from_snapshot

        try:
            snap = self.list_ui(max_depth=4, max_controls=50)
        except Exception:
            return None
        return address_control_from_snapshot(snap)

    def type_into_element(self, element: Any, text: str, *, clear: bool = True) -> dict[str, Any]:
        """Type into an already-resolved control. Must not re-look-up by id/name."""
        return self.type_text(str(text), clear=bool(clear))

    def find_files(
        self,
        name: str | None = None,
        glob: str | None = None,
        max_results: int = 20,
    ) -> dict[str, Any]:
        """Search common filesystem roots for a filename or glob."""
        from desk_pilot.desktop.files import find_files as search

        return search(name=name, glob=glob, max_results=max_results)
