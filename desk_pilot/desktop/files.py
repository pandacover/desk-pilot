"""Local file search and post-save verification (image vs HTML)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

SKIP_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "winsxs",
    "node_modules",
    ".git",
    ".svn",
    "__pycache__",
    "appdata",
}

IMAGE_EXPECT = frozenset({"image", "picture", "photo", "jpg", "jpeg", "png", "gif", "webp", "bmp"})


def default_search_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Pictures",
        home / "Videos",
        home,
    ]
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    for extra in (
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)"),
        Path(r"D:\Program Files"),
        Path(r"D:\Program Files (x86)"),
    ):
        roots.append(extra)
    roots.extend(_steam_roots())
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        try:
            resolved = str(root)
        except Exception:
            continue
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        if root.exists() and root.is_dir():
            out.append(root)
    return out


def _steam_roots() -> list[Path]:
    candidates: list[Path] = []
    env_bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramW6432"),
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "D:\\",
        "E:\\",
        "C:\\",
        str(Path.home()),
    ]
    suffixes = (
        Path("Steam") / "steamapps" / "common",
        Path("SteamLibrary") / "steamapps" / "common",
        Path("Steam") / "steamapps",
        Path(".steam") / "steam" / "steamapps" / "common",
    )
    for base in env_bases:
        if not base:
            continue
        root = Path(base)
        for suffix in suffixes:
            candidates.append(root / suffix)
            if root.name.lower() not in {"steam", "steamlibrary"}:
                candidates.append(root / "Steam" / suffix)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def find_files(
    *,
    name: str | None = None,
    glob: str | None = None,
    max_results: int = 20,
    roots: list[Path | str] | None = None,
    max_depth: int = 6,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    query_name = (name or "").strip() or None
    query_glob = (glob or "").strip() or None
    if query_name and any(ch in query_name for ch in "*?["):
        query_glob = query_name
        query_name = None
    if not query_name and not query_glob:
        return {
            "ok": False,
            "error": "find_files needs name or glob (e.g. brawlhalla.exe or *.exe).",
            "results": [],
        }
    try:
        limit = max(1, min(int(max_results), 50))
    except (TypeError, ValueError):
        limit = 20
    try:
        depth = max(1, min(int(max_depth), 10))
    except (TypeError, ValueError):
        depth = 6
    search_roots = [Path(r) for r in roots] if roots else default_search_roots()
    search_roots = [r for r in search_roots if r.exists() and r.is_dir()]
    if not search_roots:
        return {
            "ok": False,
            "error": "No searchable roots exist (user profile / Program Files / Steam).",
            "results": [],
        }
    deadline = time.time() + max(0.5, float(timeout_seconds))
    hits: list[dict[str, Any]] = []
    if os.name == "nt" and roots is None:
        hits = _powershell_search(
            search_roots,
            name=query_name,
            glob=query_glob,
            limit=limit,
            max_depth=depth,
            deadline=deadline,
        )
    if len(hits) < limit:
        seen = {h["path"].lower() for h in hits}
        for item in _pathlib_search(
            search_roots,
            name=query_name,
            glob=query_glob,
            limit=limit - len(hits),
            max_depth=depth,
            deadline=deadline,
        ):
            if item["path"].lower() in seen:
                continue
            hits.append(item)
            seen.add(item["path"].lower())
            if len(hits) >= limit:
                break
    hits.sort(key=lambda item: (0 if _is_exact_name(item["path"], query_name) else 1, item["path"].lower()))
    return {
        "ok": True,
        "results": hits[:limit],
        "count": min(len(hits), limit),
        "name": query_name,
        "glob": query_glob,
        "roots": [str(r) for r in search_roots[:12]],
        "hint": (
            "Use these full paths. Do not Win+S the filename into web search. "
            "Open Explorer only if the user asked to show a known path."
        ),
    }


def _is_exact_name(path: str, name: str | None) -> bool:
    if not name:
        return False
    return Path(path).name.lower() == name.lower()


def _match_file(path: Path, name: str | None, glob_pat: str | None) -> bool:
    base = path.name
    if glob_pat:
        return bool(fnmatch(base, glob_pat) or fnmatch(str(path), glob_pat))
    if name:
        needle = name.lower()
        return needle == base.lower() or needle in base.lower()
    return False


def _pathlib_search(
    roots: list[Path],
    *,
    name: str | None,
    glob: str | None,
    limit: int,
    max_depth: int,
    deadline: float,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for root in roots:
        if time.time() > deadline or len(hits) >= limit:
            break
        try:
            walker = os.walk(root, topdown=True, followlinks=False)
        except OSError:
            continue
        for dirpath, dirnames, filenames in walker:
            if time.time() > deadline or len(hits) >= limit:
                return hits
            try:
                rel = Path(dirpath).relative_to(root)
                depth = len(rel.parts)
            except ValueError:
                depth = 0
            dirnames[:] = [
                d
                for d in dirnames
                if d.lower() not in SKIP_DIR_NAMES and not d.startswith(".")
            ]
            if depth >= max_depth:
                dirnames.clear()
            for filename in filenames:
                if len(hits) >= limit or time.time() > deadline:
                    return hits
                path = Path(dirpath) / filename
                if not _match_file(path, name, glob):
                    continue
                hit = _stat_hit(path)
                if hit:
                    hits.append(hit)
    return hits


def _powershell_search(
    roots: list[Path],
    *,
    name: str | None,
    glob: str | None,
    limit: int,
    max_depth: int,
    deadline: float,
) -> list[dict[str, Any]]:
    remaining = max(0.4, deadline - time.time())
    filter_pat = glob or name or "*"
    if name and not glob and "*" not in name and "?" not in name:
        filter_pat = name
    root_literals = ", ".join(_ps_quote(str(r)) for r in roots[:16])
    script = (
        f"$ErrorActionPreference='SilentlyContinue'; "
        f"$roots=@({root_literals}); $filter={_ps_quote(filter_pat)}; $max={int(limit)}; "
        f"$depth={int(max_depth)}; $out=@(); "
        "foreach($root in $roots){ if(-not (Test-Path -LiteralPath $root)){ continue }; "
        "Get-ChildItem -LiteralPath $root -Recurse -File -Filter $filter -Depth $depth -ErrorAction SilentlyContinue | "
        "Select-Object -First ([Math]::Max(0,$max-$out.Count)) FullName,Length,LastWriteTimeUtc | "
        "ForEach-Object { $out += $_ }; if($out.Count -ge $max){ break } }; "
        "if($out.Count -eq 0){ '[]' } else { $out | ConvertTo-Json -Compress -AsArray }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=remaining,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = (completed.stdout or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    hits: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        path = str(item.get("FullName") or item.get("fullName") or "").strip()
        if not path:
            continue
        hit = _stat_hit(Path(path))
        if not hit:
            hit = {
                "path": path,
                "size": item.get("Length"),
                "mtime": str(item.get("LastWriteTimeUtc") or ""),
            }
        if name or glob:
            if not _match_file(Path(path), name, glob):
                continue
        hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _stat_hit(path: Path) -> dict[str, Any] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return {
        "path": str(path),
        "size": int(info.st_size),
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(info.st_mtime)),
    }


def verify_file(path: str | None, expect: str | None = "any") -> dict[str, Any]:
    target = (path or "").strip()
    if not target:
        return {"ok": False, "error": "verify_file requires path."}
    file = Path(target)
    if not file.exists():
        return {"ok": False, "error": f"No file at {target!r}.", "path": target}
    if not file.is_file():
        return {"ok": False, "error": f"{target!r} is not a file.", "path": target}
    try:
        size = file.stat().st_size
        head = file.read_bytes()[:4096]
    except OSError as exc:
        return {"ok": False, "error": f"Could not read {target!r}: {exc}", "path": target}
    kind = sniff_kind(head, size)
    wanted = (expect or "any").strip().lower() or "any"
    payload: dict[str, Any] = {
        "ok": True,
        "path": str(file),
        "size": size,
        "kind": kind,
        "expect": wanted,
    }
    if wanted in IMAGE_EXPECT:
        if kind == "html":
            payload["ok"] = False
            payload["error"] = (
                f"{file.name} is HTML, not an image (Ctrl+S on a search page saves a webpage). "
                "Open the image, use Save image as / a download control, then verify again. "
                "Try another image if needed."
            )
            return payload
        if kind == "empty" or size < 32:
            payload["ok"] = False
            payload["error"] = f"{file.name} is too small ({size} bytes) to be a real image."
            return payload
        if kind not in {"jpeg", "png", "gif", "webp", "bmp", "tiff", "image"}:
            payload["ok"] = False
            payload["error"] = (
                f"{file.name} does not look like an image (detected {kind}). "
                "A .jpg of a search page is usually HTML — do not rename a webpage to .jpg."
            )
            return payload
    return payload


def sniff_kind(data: bytes, size: int | None = None) -> str:
    if not data:
        return "empty"
    if looks_like_html(data):
        return "html"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if data[:4] in {b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"}:
        return "image"
    if size is not None and size < 32:
        return "empty"
    return "unknown"


def looks_like_html(data: bytes) -> bool:
    head = data.lstrip()[:256].lower()
    if head.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<script", b"<meta")):
        return True
    sample = head[:200]
    return b"<html" in sample or (b"<!" in sample[:20] and b"html" in sample)


def looks_like_image_path(text: str | None) -> bool:
    raw = (text or "").strip().strip('"')
    if not raw:
        return False
    suffix = Path(raw).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
        return False
    return ("\\" in raw or "/" in raw or (len(raw) >= 3 and raw[1] == ":"))


def is_image_download_goal(goal: str | None) -> bool:
    text = (goal or "").lower()
    if not text:
        return False
    wants_image = any(token in text for token in ("picture", "image", "photo", "jpg", "png", "meme"))
    wants_download = any(token in text for token in ("download", "save", "grab"))
    return wants_image and (wants_download or "download a picture" in text or "download an image" in text)


def is_find_file_goal(goal: str | None) -> bool:
    text = (goal or "").lower()
    if not text:
        return False
    if is_image_download_goal(text):
        return False
    locate = any(token in text for token in ("find ", "locate ", "where is", "on my computer", "on this pc"))
    fileish = any(token in text for token in (".exe", "file", "folder", "install")) or bool(
        re.search(r"\b\w+\.(exe|dll|msi|png|jpg|pdf|zip)\b", text)
    )
    return locate and (fileish or ".exe" in text)
