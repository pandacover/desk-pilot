"""Dry-run desktop: a tiny fake Windows session so Linux CI can exercise the loop."""

from __future__ import annotations

import time
from typing import Any

from desk_pilot.app.config import screenshot_dir
from desk_pilot.desktop.base import DesktopBackend


PLACEHOLDER_TRUNCATED