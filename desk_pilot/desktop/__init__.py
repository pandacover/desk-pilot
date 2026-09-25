"""Desktop backends: Windows UI Automation, or a dry-run stub."""

from __future__ import annotations

import sys

from desk_pilot.desktop.base import DesktopBackend


def get_backend(*, force_mock: bool = False) -> DesktopBackend:
    if force_mock or sys.platform != "win32":
        from desk_pilot.desktop.mock import MockDesktop

        return MockDesktop()
    from desk_pilot.desktop.windows import WindowsDesktop

    return WindowsDesktop()


def is_windows() -> bool:
    return sys.platform == "win32"
