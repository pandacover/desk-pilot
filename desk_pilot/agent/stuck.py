"""Force a strategy change when the agent grinds the same failing act."""

from __future__ import annotations

import json
from typing import Any

OBSERVE_TOOLS = frozenset({"list_ui", "list_windows", "wait_for_window"})
IGNORE_TOOLS = frozenset({"done", "fail", "guide_step"})

STUCK_MESSAGE = (
    "STUCK: the same tool+args failed, or the UI snapshot fingerprint did not change, "
    "for 3 consecutive acts. You MUST change strategy now: pick a different tool "
    "(find_files if locating a file/.exe on this computer, screenshot_region to see the "
    "real screen, or fail with a concrete reason). Do not grind Explorer/Start search "
    "when the search box is not focused. Do not Win+S a filename into Edge web search."
)


def snapshot_fingerprint(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
    focused = snapshot.get("focused") if isinstance(snapshot.get("focused"), dict) else {}
    parts = [
        str((window or {}).get("name") or ""),
        str((window or {}).get("class") or ""),
        str((window or {}).get("process") or ""),
        str((focused or {}).get("name") or ""),
        str((focused or {}).get("type") or ""),
        str((focused or {}).get("automation_id") or ""),
    ]
    for item in (snapshot.get("controls") or [])[:12]:
        if not isinstance(item, dict):
            continue
        parts.append(str(item.get("name") or ""))
        parts.append(str(item.get("type") or ""))
        parts.append(str(item.get("automation_id") or ""))
    return "|".join(parts).lower()


def canonical_args(name: str, args: dict[str, Any] | None) -> str:
    data: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value is None or value == "":
            continue
        if key in {"x", "y", "x1", "y1", "x2", "y2", "width", "height"}:
            try:
                data[key] = int(round(float(value) / 10.0) * 10)
            except (TypeError, ValueError):
                data[key] = value
        elif key == "text":
            data[key] = str(value).strip().lower()[:80]
        else:
            data[key] = value
    try:
        return json.dumps({"name": name, "args": data}, sort_keys=True, default=str)
    except TypeError:
        return f"{name}:{data}"


def action_signature(events: list[dict[str, Any]]) -> str:
    chosen: dict[str, Any] | None = None
    for event in events:
        name = str(event.get("name") or "")
        if name in IGNORE_TOOLS:
            continue
        if name not in OBSERVE_TOOLS:
            chosen = event
    if chosen is None:
        for event in reversed(events):
            if str(event.get("name") or "") not in IGNORE_TOOLS:
                chosen = event
                break
    if not chosen:
        return ""
    return canonical_args(str(chosen.get("name") or ""), chosen.get("args") if isinstance(chosen.get("args"), dict) else {})


class StuckTracker:
    def __init__(self) -> None:
        self.streak = 0
        self.last_fp = ""
        self.last_sig = ""
        self.forced = False

    def note(
        self,
        events: list[dict[str, Any]],
        snapshot: dict[str, Any] | None,
    ) -> bool:
        if not events:
            return False
        meaningful = [e for e in events if str(e.get("name") or "") not in IGNORE_TOOLS]
        if not meaningful:
            return False
        fp = snapshot_fingerprint(snapshot)
        sig = action_signature(meaningful)
        failed = any(not bool(e.get("ok", True)) for e in meaningful if str(e.get("name") or "") not in OBSERVE_TOOLS)
        if not any(str(e.get("name") or "") not in OBSERVE_TOOLS for e in meaningful):
            failed = any(not bool(e.get("ok", True)) for e in meaningful)
        unchanged = bool(self.last_fp) and fp == self.last_fp
        same_fail = bool(self.last_sig) and sig == self.last_sig and (failed or unchanged)
        if unchanged or same_fail:
            self.streak += 1
        else:
            self.streak = 0
            self.forced = False
        self.last_fp = fp
        self.last_sig = sig
        self.forced = self.streak >= 3
        return self.forced

    def message(self) -> str:
        return STUCK_MESSAGE
