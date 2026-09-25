"""Find and start an installed Windows app by display name (no hardcoded paths)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


def score_name(query: str, candidate: str) -> int:
    q = _norm(query)
    n = _norm(candidate)
    if not q or not n:
        return 0
    if n == q:
        return 100
    if n.startswith(q) or q.startswith(n):
        return 85
    if q in n:
        return 70
    if n in q and len(n) >= 3:
        return 55
    q_tokens = set(q.split())
    n_tokens = set(n.split())
    if q_tokens and q_tokens <= n_tokens:
        return 80
    return 0


def window_match_score(query: str, *, title: str = "", process: str = "") -> int:
    """Score how well an open window matches an app name (title or process stem)."""
    best = max(score_name(query, title), score_name(query, process))
    q = _norm(query)
    t = _norm(title)
    p = _norm(process)
    if q and q in t:
        best = max(best, 88)
    if q and p and (q == p or q in p.split()):
        best = max(best, 95)
    return best


def is_agent_window(title: str = "", process: str = "") -> bool:
    blob = f"{title} {process}".lower()
    return "desk pilot" in blob


def best_named_match(query: str, names: Iterable[str], *, minimum: int = 55) -> str | None:
    ranked = sorted(((score_name(query, name), name) for name in names), reverse=True)
    if not ranked or ranked[0][0] < minimum:
        return None
    return ranked[0][1]


def detect_run_not_found(window: dict[str, Any] | None, controls: list[dict[str, Any]] | None) -> str | None:
    """Return the dialog text if this looks like Win+R 'Windows cannot find'."""
    parts: list[str] = []
    if window:
        parts.extend(str(window.get(k) or "") for k in ("name", "class", "error"))
    for item in controls or []:
        parts.append(str(item.get("name") or ""))
        parts.append(str(item.get("type") or ""))
    blob = " ".join(parts)
    lower = blob.lower()
    if "cannot find" in lower or "windows cannot find" in lower:
        return " ".join(blob.split())[:400]
    return None


def which_candidates(query: str) -> list[str]:
    raw = (query or "").strip().strip('"')
    if not raw:
        return []
    names = [raw]
    if not raw.lower().endswith(".exe"):
        names.append(raw + ".exe")
    found: list[str] = []
    seen: set[str] = set()
    for name in names:
        path = Path(name).expanduser()
        if path.is_file():
            _add(found, seen, str(path))
        located = shutil.which(name)
        if located:
            _add(found, seen, located)
    return found


def app_path_candidates(query: str) -> list[str]:
    if sys.platform != "win32":
        return []
    import winreg

    raw = (query or "").strip().strip('"')
    if not raw:
        return []
    exe = raw if raw.lower().endswith(".exe") else raw + ".exe"
    roots = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    found: list[str] = []
    seen: set[str] = set()
    for hive, base in roots:
        direct = rf"{base}\{exe}"
        value = _reg_default(hive, direct)
        if value:
            _add(found, seen, value)
        try:
            with winreg.OpenKey(hive, base) as key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    if score_name(raw, Path(sub).stem) < 70:
                        continue
                    value = _reg_default(hive, rf"{base}\{sub}")
                    if value:
                        _add(found, seen, value)
        except OSError:
            continue
    return found


def start_menu_shortcuts(query: str, *, limit: int = 8) -> list[str]:
    roots = [
        Path(os.environ.get("APPDATA") or "") / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "Microsoft/Windows/Start Menu/Programs",
    ]
    scored: list[tuple[int, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*.lnk"):
                score = max(score_name(query, path.stem), score_name(query, path.name))
                if score >= 55:
                    scored.append((score, str(path)))
        except OSError:
            continue
    scored.sort(reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for _score, path in scored:
        if path not in seen:
            seen.add(path)
            out.append(path)
        if len(out) >= limit:
            break
    return out


def start_apps_catalog(*, timeout: float = 12.0) -> list[dict[str, str]]:
    if sys.platform != "win32":
        return []
    command = (
        "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return []
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    apps: list[dict[str, str]] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        app_id = str(item.get("AppID") or "").strip()
        if name and app_id:
            apps.append({"name": name, "appid": app_id})
    return apps


def match_start_app(query: str, apps: list[dict[str, str]]) -> dict[str, str] | None:
    ranked: list[tuple[int, dict[str, str]]] = []
    for app in apps:
        ranked.append((max(score_name(query, app["name"]), score_name(query, app["appid"])), app))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 55:
        return None
    return ranked[0][1]


def start_file(path: str) -> bool:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([path], close_fds=True)
        return True
    except OSError:
        return False


def start_apps_folder(app_id: str) -> bool:
    if sys.platform != "win32" or not app_id:
        return False
    target = f"shell:AppsFolder\\{app_id}"
    try:
        os.startfile(target)  # type: ignore[attr-defined]
        return True
    except OSError:
        try:
            subprocess.Popen(["explorer.exe", target], close_fds=True)
            return True
        except OSError:
            return False


def _norm(value: str) -> str:
    return " ".join((value or "").replace(".exe", " ").replace("_", " ").replace("-", " ").lower().split())


def _add(found: list[str], seen: set[str], value: str) -> None:
    cleaned = value.strip().strip('"')
    if cleaned and cleaned.lower() not in seen:
        seen.add(cleaned.lower())
        found.append(cleaned)


def _reg_default(hive: Any, path: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(hive, path) as key:
            value, _typ = winreg.QueryValueEx(key, "")
    except OSError:
        return None
    text = str(value or "").strip().strip('"')
    if text and Path(text).is_file():
        return text
    return text or None
