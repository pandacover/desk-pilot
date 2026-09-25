from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DesktopBackend(ABC):
    """UIA-first desktop control. Mock impl is used off Windows."""

    dry_run: bool = False
    highlight_overlay: bool = True

    def flash_highlight(self, rect: list[int] | tuple[int, ...] | None, duration: float | None = None) -> None:
        """Briefly sketch a UIA bounding rect. No-op when disabled or off Windows."""
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
