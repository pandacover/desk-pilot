from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from desk_pilot import APP_ID, DEFAULT_MAX_STEPS, DEFAULT_MODEL


def config_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return root / APP_ID
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "desk-pilot"
    if sys_is_macos():
        return Path.home() / "Library" / "Application Support" / APP_ID
    return Path.home() / ".config" / "desk-pilot"


def sys_is_macos() -> bool:
    return os.uname().sysname == "Darwin" if hasattr(os, "uname") else False


def cache_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return root / APP_ID
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "desk-pilot"
    return Path.home() / ".cache" / "desk-pilot"


def config_path() -> Path:
    return config_dir() / "config.json"


def screenshot_dir() -> Path:
    path = cache_dir() / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    openrouter_api_key: str = ""
    model: str = DEFAULT_MODEL
    max_steps: int = DEFAULT_MAX_STEPS
    reasoning_effort: str = "low"
    confirm_before_run: bool = True

    def masked_key(self) -> str:
        key = self.effective_api_key()
        if not key:
            return ""
        if len(key) <= 8:
            return "••••"
        return f"{key[:4]}…{key[-4:]}"

    def effective_api_key(self) -> str:
        env = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if env:
            return env
        return (self.openrouter_api_key or "").strip()

    def key_from_env(self) -> bool:
        return bool((os.environ.get("OPENROUTER_API_KEY") or "").strip())

    def to_disk_dict(self) -> dict:
        return {
            "openrouter_api_key": self.openrouter_api_key,
            "model": self.model or DEFAULT_MODEL,
            "max_steps": int(self.max_steps) or DEFAULT_MAX_STEPS,
            "reasoning_effort": self.reasoning_effort or "low",
            "confirm_before_run": bool(self.confirm_before_run),
        }


def load_settings(path: Path | None = None) -> Settings:
    file = path or config_path()
    if not file.is_file():
        return Settings()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    max_steps = data.get("max_steps", DEFAULT_MAX_STEPS)
    try:
        max_steps = int(max_steps)
    except (TypeError, ValueError):
        max_steps = DEFAULT_MAX_STEPS
    max_steps = max(1, min(max_steps, 80))
    effort = str(data.get("reasoning_effort") or "low").lower()
    if effort not in {"none", "minimal", "low"}:
        effort = "low"
    return Settings(
        openrouter_api_key=str(data.get("openrouter_api_key") or ""),
        model=str(data.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        max_steps=max_steps,
        reasoning_effort=effort,
        confirm_before_run=bool(data.get("confirm_before_run", True)),
    )


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    file = path or config_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.to_disk_dict()
    file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return file
