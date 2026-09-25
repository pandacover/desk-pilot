"""Address-bar navigation: atomic Chromium navigate + stale title / UIA verify."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

NAV_TIMEOUT_LIVE = 2.5
NAV_TIMEOUT_DRY = 0.25
ADDRESS_HINTS = (
    "address",
    "url",
    "omnibox",
    "search bar",
    "location",
    "urlbar",
    "address and search",
)
GENERIC_TOKENS = {
    "www",
    "http",
    "https",
    "com",
    "net",
    "org",
    "html",
    "htm",
    "php",
    "index",
    "the",
    "and",
    "search",
}
ADDRESS_CONTROL_TYPES = {"edit", "combobox"}
CHROMIUM_FAMILY_HINTS = (
    "chrome",
    "msedge",
    "msedgewebview2",
    "helium",
    "chromium",
    "brave",
    "opera",
    "vivaldi",
    "arc",
    "edgium",
    "chrome_widgetwin",
    "chrome_renderwidgethosthwnd",
)


def is_url_like(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw or "\n" in raw:
        return False
    lower = raw.lower()
    if lower.startswith(("http://", "https://", "www.")):
        return True
    if " " in raw.strip():
        return False
    if "/" in raw and "." in raw:
        return True
    hostish = raw.split("/")[0]
    if hostish.count(".") >= 1 and any(
        hostish.lower().endswith(suf) for suf in (".com", ".net", ".org", ".io", ".co", ".edu", ".gov")
    ):
        return True
    return False


def focused_looks_like_address(snapshot: dict[str, Any] | None) -> bool:
    if not snapshot:
        return False
    focused = snapshot.get("focused") if isinstance(snapshot.get("focused"), dict) else {}
    blob = " ".join(
        str((focused or {}).get(key) or "")
        for key in ("name", "automation_id", "path", "type")
    ).lower()
    if any(hint in blob for hint in ADDRESS_HINTS):
        return True
    for item in snapshot.get("controls") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        aid = str(item.get("automation_id") or "").lower()
        if any(hint in f"{name} {aid}" for hint in ADDRESS_HINTS):
            # Only treat as address-bar typing if that control is focused-ish
            if focused and (
                str(focused.get("name") or "").lower() == name
                or str(focused.get("automation_id") or "").lower() == aid
            ):
                return True
    return False


def looks_like_navigation(text: str | None, snapshot: dict[str, Any] | None = None) -> bool:
    if is_url_like(text):
        return True
    return bool(text and text.strip()) and focused_looks_like_address(snapshot)


def extract_address_value(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    focused = snapshot.get("focused") if isinstance(snapshot.get("focused"), dict) else {}
    value = str((focused or {}).get("value") or "").strip()
    if value:
        return value[:240]
    for item in snapshot.get("controls") or []:
        if not isinstance(item, dict):
            continue
        blob = f"{item.get('name') or ''} {item.get('automation_id') or ''} {item.get('path') or ''}".lower()
        if any(hint in blob for hint in ADDRESS_HINTS):
            raw = str(item.get("value") or "").strip()
            if raw:
                return raw[:240]
    return ""


def window_title(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
    return str((window or {}).get("name") or "")


def nav_fingerprint(snapshot: dict[str, Any] | None) -> dict[str, str]:
    focused = {}
    if snapshot and isinstance(snapshot.get("focused"), dict):
        focused = snapshot.get("focused") or {}
    return {
        "title": window_title(snapshot),
        "address": extract_address_value(snapshot),
        "focused": str((focused or {}).get("name") or ""),
    }


def _window_blob(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return ""
    window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
    parts = [
        str((window or {}).get("name") or ""),
        str((window or {}).get("process") or ""),
        str((window or {}).get("class") or ""),
    ]
    return " ".join(parts).lower()


def chromium_family_status(snapshot: dict[str, Any] | None) -> tuple[bool, str]:
    """Chromium-family first (Helium / Chrome / Edge). Other browsers are a clear miss."""
    window = snapshot.get("window") if snapshot and isinstance(snapshot.get("window"), dict) else {}
    name = str((window or {}).get("name") or "").strip()
    process = str((window or {}).get("process") or "").strip()
    klass = str((window or {}).get("class") or "").strip()
    observed = name or process or klass or "unknown window"
    if process:
        observed = f"{name or 'window'} ({process})"
    blob = _window_blob(snapshot)
    if any(hint in blob for hint in CHROMIUM_FAMILY_HINTS):
        return True, observed
    title = name.lower()
    if title.endswith(" - helium") or " - google chrome" in title or title.endswith(" - microsoft edge"):
        return True, observed
    return False, observed


def address_control_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """Best Chromium address Edit/ComboBox from a compact list_ui tree."""
    if not snapshot:
        return None
    best: dict[str, Any] | None = None
    best_score = 0
    for item in snapshot.get("controls") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        aid = str(item.get("automation_id") or "").lower()
        path = str(item.get("path") or "").lower()
        ctype = str(item.get("type") or "").lower()
        blob = f"{name} {aid} {path}"
        if not any(hint in blob for hint in ADDRESS_HINTS):
            continue
        if ctype and ctype not in ADDRESS_CONTROL_TYPES and "edit" not in ctype and "combo" not in ctype:
            continue
        score = 0
        if "address and search" in name:
            score += 6
        elif "address" in name:
            score += 4
        if aid in {"urlbar", "omnibox"} or "urlbar" in aid:
            score += 5
        if any(hint in blob for hint in ADDRESS_HINTS):
            score += 1
        if ctype in ADDRESS_CONTROL_TYPES:
            score += 1
        if score > best_score:
            best_score = score
            best = item
    if best:
        return best
    focused = snapshot.get("focused") if isinstance(snapshot.get("focused"), dict) else {}
    if focused and focused_looks_like_address(snapshot):
        ctype = str((focused or {}).get("type") or "").lower()
        if not ctype or ctype in ADDRESS_CONTROL_TYPES or "edit" in ctype or "combo" in ctype:
            return dict(focused)
    return None


def _nav_payload(
    *,
    ok: bool,
    reason: str,
    target: str,
    before: dict[str, str] | None = None,
    after: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before = dict(before or {})
    after = dict(after or {})
    payload: dict[str, Any] = {
        "ok": ok,
        "nav_ok": ok,
        "nav_failed": not ok,
        "reason": reason,
        "target": target,
        "before": before,
        "after": after,
        "title": after.get("title") or before.get("title") or "",
        "address": (after.get("address") or before.get("address") or "")[:240],
    }
    if extra:
        payload.update(extra)
    return payload


def _snapshot(backend: Any) -> dict[str, Any] | None:
    try:
        snap = backend.list_ui(max_depth=4, max_controls=50)
    except Exception:
        return None
    return snap if isinstance(snap, dict) else None


def run_navigate(
    backend: Any,
    url: str,
    *,
    title_contains: str | None = None,
    process_contains: str | None = None,
    log=None,
) -> dict[str, Any]:
    """Atomic Chromium address-bar navigation: focus, type URL, Enter, verify."""
    target = (url or "").strip()
    if not target:
        return _nav_payload(ok=False, reason="navigate requires a url.", target="")

    if title_contains or process_contains:
        try:
            focused = backend.focus_window(
                title_contains=title_contains,
                process_contains=process_contains,
            )
        except Exception as exc:
            return _nav_payload(
                ok=False,
                reason=f"navigate could not focus the browser window: {exc}",
                target=target,
            )
        if isinstance(focused, dict) and not focused.get("ok", True):
            return _nav_payload(
                ok=False,
                reason=str(focused.get("error") or "navigate could not focus the browser window."),
                target=target,
                extra={"focus": focused},
            )

    snapshot = _snapshot(backend)
    is_chromium, observed = chromium_family_status(snapshot)
    if not is_chromium:
        after = nav_fingerprint(snapshot)
        return _nav_payload(
            ok=False,
            reason=(
                "navigate supports Chromium-family browsers (Helium, Chrome, Edge). "
                f"Foreground is {observed}."
            ),
            target=target,
            before=after,
            after=after,
        )

    address = address_control_from_snapshot(snapshot)
    if not address:
        after = nav_fingerprint(snapshot)
        return _nav_payload(
            ok=False,
            reason="missing address control: no Chromium address Edit/ComboBox in the UIA tree",
            target=target,
            before=after,
            after=after,
        )

    aid = str(address.get("automation_id") or "").strip() or None
    name = str(address.get("name") or "").strip() or None
    if log:
        log("act", f"NAV address control: name={name!r} automation_id={aid!r}")
    try:
        backend.click(automation_id=aid, name=name)
    except Exception:
        pass
    try:
        typed = backend.type_text(str(target), automation_id=aid, name=name, clear=True)
    except Exception as exc:
        after = nav_fingerprint(_snapshot(backend))
        return _nav_payload(
            ok=False,
            reason=f"navigate could not type the URL: {exc}",
            target=target,
            after=after,
        )
    if isinstance(typed, dict) and typed.get("ok") is False:
        after = nav_fingerprint(_snapshot(backend))
        return _nav_payload(
            ok=False,
            reason=str(typed.get("error") or "navigate could not type the URL."),
            target=target,
            after=after,
            extra={"typed": typed},
        )

    before = nav_fingerprint(_snapshot(backend) or snapshot)
    try:
        enter_result = backend.hotkey("enter")
    except Exception as exc:
        enter_result = {"ok": False, "error": f"enter failed: {exc}"}
    if not isinstance(enter_result, dict):
        enter_result = {"ok": bool(enter_result)}

    checked = verify_enter_navigation(backend, target, before, enter_result, log=log)
    after = checked.get("after") if isinstance(checked.get("after"), dict) else nav_fingerprint(_snapshot(backend))
    checked["title"] = str((after or {}).get("title") or checked.get("title") or "")
    checked["address"] = str((after or {}).get("address") or checked.get("address") or "")[:240]
    checked["target"] = target
    checked["window"] = checked["title"]
    if checked.get("nav_ok"):
        checked["ok"] = True
        checked["nav_failed"] = False
    else:
        checked["ok"] = False
        checked["nav_ok"] = False
        checked["nav_failed"] = True
    return checked


def target_tokens(target: str) -> list[str]:
    raw = (target or "").strip()
    if not raw:
        return []
    tokens: list[str] = []
    candidate = raw
    if "://" not in candidate and is_url_like(candidate):
        candidate = "http://" + candidate
    parsed = urlparse(candidate if "://" in candidate else "")
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").strip("/")
    for part in host.replace("-", ".").split("."):
        if part and part not in GENERIC_TOKENS and len(part) > 1:
            tokens.append(part)
    for part in path.replace("-", "/").split("/"):
        chunk = part.strip().lower()
        if chunk and chunk not in GENERIC_TOKENS and len(chunk) > 2 and chunk.isalnum():
            tokens.append(chunk)
    if not tokens:
        for chunk in re_split(raw.lower()):
            if chunk and chunk not in GENERIC_TOKENS and len(chunk) > 2:
                tokens.append(chunk)
    # de-dupe, keep order
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out[:8]


def re_split(text: str) -> list[str]:
    buf: list[str] = []
    cur: list[str] = []
    for ch in text:
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            buf.append("".join(cur))
            cur = []
    if cur:
        buf.append("".join(cur))
    return buf


def nav_changed_toward(
    before: dict[str, str] | None,
    after: dict[str, str] | None,
    target: str,
) -> tuple[bool, str]:
    before = before or {}
    after = after or {}
    old_title = (before.get("title") or "").strip()
    new_title = (after.get("title") or "").strip()
    old_addr = (before.get("address") or "").strip()
    new_addr = (after.get("address") or "").strip()
    title_changed = bool(new_title) and new_title.lower() != old_title.lower()
    addr_changed = bool(new_addr) and new_addr.lower() != old_addr.lower()
    hay = f"{new_title} {new_addr}".lower()
    tokens = target_tokens(target)
    matched = [tok for tok in tokens if tok in hay]
    if not title_changed and not addr_changed:
        return False, (
            f"stale navigation: title still {old_title!r} and address-bar UIA value unchanged "
            f"after going to {target!r}"
        )
    if tokens and not matched:
        return False, (
            f"navigation mismatch: title {new_title!r} / address {new_addr!r} "
            f"does not reflect {target!r} (wanted tokens {tokens})"
        )
    if matched or title_changed or addr_changed:
        return True, f"nav_ok: title {new_title!r} address {new_addr!r} toward {target!r}"
    return False, f"stale navigation: title still {new_title!r} after {target!r}"


def wait_for_navigation(
    backend: Any,
    target: str,
    before: dict[str, str],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    dry = bool(getattr(backend, "dry_run", False))
    timeout = NAV_TIMEOUT_DRY if timeout_seconds is None and dry else (
        NAV_TIMEOUT_LIVE if timeout_seconds is None else float(timeout_seconds)
    )
    deadline = time.time() + max(0.05, timeout)
    last = dict(before)
    reason = f"stale navigation: title still {before.get('title')!r} after {target!r}"
    while True:
        try:
            snapshot = backend.list_ui(max_depth=3, max_controls=40)
        except Exception:
            snapshot = None
        last = nav_fingerprint(snapshot)
        ok, reason = nav_changed_toward(before, last, target)
        if ok:
            return {
                "nav_ok": True,
                "nav_failed": False,
                "reason": reason,
                "before": before,
                "after": last,
                "target": target,
            }
        if time.time() >= deadline:
            break
        time.sleep(0.08 if dry else 0.2)
    return {
        "nav_ok": False,
        "nav_failed": True,
        "reason": reason,
        "before": before,
        "after": last,
        "target": target,
    }


def retry_navigation(backend: Any, target: str, log=None) -> dict[str, Any]:
    """ctrl+l, clear, retype URL, enter — then verify again."""
    if log:
        log("act", f"NAV retry: ctrl+l, retype {target!r}, enter")
    try:
        backend.hotkey("ctrl+l")
    except Exception as exc:
        if log:
            log("error", f"NAV retry ctrl+l failed: {exc}")
    try:
        backend.type_text(str(target), clear=True)
    except Exception as exc:
        if log:
            log("error", f"NAV retry type failed: {exc}")
    before = nav_fingerprint(getattr(backend, "list_ui", lambda: {})())
    try:
        backend.hotkey("enter")
    except Exception as exc:
        if log:
            log("error", f"NAV retry enter failed: {exc}")
    return wait_for_navigation(backend, target, before)


def screenshot_focused_window(backend: Any) -> dict[str, Any] | None:
    box = None
    try:
        box = backend.focused_window_rect()
    except Exception:
        box = None
    if not box or len(box) < 4:
        try:
            snap = backend.list_ui(max_depth=2, max_controls=8)
            window = snap.get("window") if isinstance(snap, dict) else {}
            box = (window or {}).get("rect")
        except Exception:
            box = None
    if not box or len(box) < 4:
        return None
    left, top, right, bottom = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    width, height = max(1, right - left), max(1, bottom - top)
    try:
        return backend.screenshot_region(left, top, width, height)
    except Exception:
        return None


def verify_enter_navigation(
    backend: Any,
    target: str,
    before: dict[str, str],
    hotkey_result: dict[str, Any],
    *,
    log=None,
) -> dict[str, Any]:
    """Post-check after address-bar Enter. Retry once, then screenshot. Never claim success on a stale title."""
    result = dict(hotkey_result) if isinstance(hotkey_result, dict) else {"ok": bool(hotkey_result)}
    verdict = wait_for_navigation(backend, target, before)
    if verdict.get("nav_ok"):
        result.update(verdict)
        result["ok"] = True
        return result
    reason = str(verdict.get("reason") or "stale navigation")
    if log:
        log("error", f"NAV failed: {reason}")
    retry = retry_navigation(backend, target, log=log)
    if retry.get("nav_ok"):
        result.update(retry)
        result["ok"] = True
        result["nav_retried"] = True
        if log:
            log("act", "NAV retry succeeded")
        return result
    shot = screenshot_focused_window(backend)
    fail_reason = str(retry.get("reason") or reason)
    if log:
        log("error", f"NAV failed after retry: {fail_reason}")
    result.update(retry)
    result["ok"] = False
    result["nav_ok"] = False
    result["nav_failed"] = True
    result["nav_retried"] = True
    result["error"] = fail_reason
    result["reason"] = fail_reason
    if shot and shot.get("ok") and shot.get("path"):
        result["screenshot"] = shot.get("path")
        result["hint"] = "Do not claim the page loaded. Title/address were stale; inspect the screenshot."
    else:
        result["hint"] = "Do not claim the page loaded — window title/address did not change toward the URL."
    return result
