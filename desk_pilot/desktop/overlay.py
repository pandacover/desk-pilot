"""Reuse one click-through, topmost layered overlay for the sketch highlight."""

from __future__ import annotations

import sys
import time
from typing import Any, Sequence

HIGHLIGHT_SECONDS = 0.4

_singleton: HighlightOverlay | None = None


class HighlightOverlay:
    """Win32 WS_EX_LAYERED + TRANSPARENT + NOACTIVATE host. No-op off Windows."""

    def __init__(self) -> None:
        self._hwnd = 0
        self._class_atom = 0

    def show(self, rect: Sequence[float] | None) -> dict[str, Any]:
        """Paint the sketch and leave it up until hide(). Click-through, no focus steal."""
        from desk_pilot.desktop.rects import SKIP_NO_RECT, as_rect
        from desk_pilot.desktop.sketch import render_sketch

        box = as_rect(rect)
        if not box:
            return {"ok": False, "skipped": True, "error": SKIP_NO_RECT, "rect": None}
        if sys.platform != "win32":
            return {
                "ok": False,
                "skipped": True,
                "error": "overlay only on Windows",
                "rect": box,
            }
        try:
            image, origin_x, origin_y = render_sketch(box)
        except Exception as exc:  # noqa: BLE001 — Pillow/geometry must surface
            return {
                "ok": False,
                "skipped": False,
                "error": f"render_sketch {type(exc).__name__}: {exc}",
                "rect": box,
            }
        if image.width < 2 or image.height < 2:
            return {
                "ok": False,
                "skipped": False,
                "error": f"render_sketch produced {image.width}x{image.height} image",
                "rect": box,
            }
        hwnd, err = self._ensure_window()
        if not hwnd:
            return {
                "ok": False,
                "skipped": False,
                "error": err or "CreateWindowExW failed",
                "rect": box,
            }
        ok, blit_err = _blit(hwnd, image, origin_x, origin_y)
        if not ok:
            return {
                "ok": False,
                "skipped": False,
                "error": blit_err or "UpdateLayeredWindow failed",
                "rect": box,
            }
        return {"ok": True, "skipped": False, "error": None, "rect": box}

    def flash(self, rect: Sequence[float] | None, *, duration: float = HIGHLIGHT_SECONDS) -> dict[str, Any]:
        """Brief show+hide (unused by auto-act; kept for tests)."""
        result = self.show(rect)
        if not result.get("ok"):
            return result
        wait = max(0.0, min(1.0, float(duration)))
        if wait:
            time.sleep(wait)
        self.hide()
        return result

    def hide(self) -> None:
        if sys.platform != "win32" or not self._hwnd:
            return
        try:
            ctypes = _ctypes()
            ctypes.windll.user32.ShowWindow(self._hwnd, 0)  # SW_HIDE
        except Exception:
            return

    def close(self) -> None:
        if sys.platform != "win32" or not self._hwnd:
            self._hwnd = 0
            return
        try:
            ctypes = _ctypes()
            ctypes.windll.user32.DestroyWindow(self._hwnd)
        except Exception:
            pass
        self._hwnd = 0

    def _ensure_window(self) -> tuple[int, str | None]:
        if sys.platform != "win32":
            return 0, "overlay only on Windows"
        if self._hwnd:
            return self._hwnd, None
        try:
            hwnd, err = _create_overlay_hwnd()
        except Exception as exc:  # noqa: BLE001 — Win32 class/window create must surface
            self._hwnd = 0
            return 0, f"{type(exc).__name__}: {exc}"
        self._hwnd = hwnd
        return hwnd, err


def get_overlay() -> HighlightOverlay:
    global _singleton
    if _singleton is None:
        _singleton = HighlightOverlay()
    return _singleton


def close_overlay() -> None:
    global _singleton
    if _singleton is not None:
        _singleton.close()
        _singleton = None


def _ctypes():  # lazy so Linux imports stay cheap
    import ctypes

    return ctypes


def _wintype_attr(mod: Any, name: str, fallback: Any) -> Any:
    """Read a ctypes.wintypes alias; some Python/Windows builds omit HCURSOR/HICON/…"""
    try:
        value = getattr(mod, name)
    except (AttributeError, ImportError):
        return fallback
    return fallback if value is None else value


def overlay_wintypes(wintypes_mod: Any | None = None) -> Any:
    """HANDLE-safe wintypes for WNDCLASSW / CreateWindowEx / UpdateLayeredWindow."""
    import ctypes
    from types import SimpleNamespace

    if wintypes_mod is None:
        from ctypes import wintypes as wintypes_mod

    handle = _wintype_attr(wintypes_mod, "HANDLE", ctypes.c_void_p)
    return SimpleNamespace(
        UINT=_wintype_attr(wintypes_mod, "UINT", ctypes.c_uint),
        DWORD=_wintype_attr(wintypes_mod, "DWORD", ctypes.c_ulong),
        WORD=_wintype_attr(wintypes_mod, "WORD", ctypes.c_ushort),
        BOOL=_wintype_attr(wintypes_mod, "BOOL", ctypes.c_int),
        HWND=_wintype_attr(wintypes_mod, "HWND", handle),
        HINSTANCE=_wintype_attr(wintypes_mod, "HINSTANCE", handle),
        HICON=_wintype_attr(wintypes_mod, "HICON", handle),
        HCURSOR=_wintype_attr(wintypes_mod, "HCURSOR", handle),
        HBRUSH=_wintype_attr(wintypes_mod, "HBRUSH", handle),
        HMENU=_wintype_attr(wintypes_mod, "HMENU", handle),
        HBITMAP=_wintype_attr(wintypes_mod, "HBITMAP", handle),
        HDC=_wintype_attr(wintypes_mod, "HDC", handle),
        LPCWSTR=_wintype_attr(wintypes_mod, "LPCWSTR", ctypes.c_wchar_p),
        LPVOID=_wintype_attr(wintypes_mod, "LPVOID", ctypes.c_void_p),
        WPARAM=_wintype_attr(wintypes_mod, "WPARAM", ctypes.c_size_t),
        LPARAM=_wintype_attr(wintypes_mod, "LPARAM", ctypes.c_ssize_t),
    )


def wndclassw_type(wintypes_mod: Any | None = None) -> type:
    """WNDCLASSW Structure whose field types never raise AttributeError on missing aliases."""
    import ctypes

    wt = overlay_wintypes(wintypes_mod)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wt.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE),
            ("hIcon", wt.HICON),
            ("hCursor", wt.HCURSOR),
            ("hbrBackground", wt.HBRUSH),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
        ]

    return WNDCLASSW


def _win_error(label: str) -> str:
    import ctypes

    err = ctypes.get_last_error()
    detail = ""
    try:
        detail = (ctypes.FormatError(err) or "").strip()
    except Exception:
        detail = ""
    if detail:
        return f"{label} (GetLastError={err} {detail})"
    return f"{label} (GetLastError={err})"


def _create_overlay_hwnd() -> tuple[int, str | None]:
    import ctypes

    wt = overlay_wintypes()
    WNDCLASSW = wndclassw_type()

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_POPUP = 0x80000000
    CS_HREDRAW = 0x0002
    CS_VREDRAW = 0x0001
    ERROR_CLASS_ALREADY_EXISTS = 1410

    try:
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        pass

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    @WNDPROC
    def _wndproc(hwnd, msg, wparam, lparam):
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # Keep the callback alive for the process lifetime.
    global _wndproc_ref
    _wndproc_ref = _wndproc

    class_name = "DeskPilotHighlightOverlay"
    hinstance = kernel32.GetModuleHandleW(None)
    wnd = WNDCLASSW()
    wnd.style = CS_HREDRAW | CS_VREDRAW
    wnd.lpfnWndProc = ctypes.cast(_wndproc, ctypes.c_void_p)
    wnd.hInstance = hinstance
    wnd.lpszClassName = class_name
    atom = user32.RegisterClassW(ctypes.byref(wnd))
    if not atom:
        err = ctypes.get_last_error()
        if err != ERROR_CLASS_ALREADY_EXISTS:
            return 0, _win_error("RegisterClassW failed")

    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [
        wt.DWORD,
        wt.LPCWSTR,
        wt.LPCWSTR,
        wt.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wt.HWND,
        wt.HMENU,
        wt.HINSTANCE,
        wt.LPVOID,
    ]
    ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    hwnd = user32.CreateWindowExW(
        ex,
        class_name,
        "Desk Pilot highlight",
        WS_POPUP,
        0,
        0,
        8,
        8,
        None,
        None,
        hinstance,
        None,
    )
    if not hwnd:
        return 0, _win_error("CreateWindowExW failed")
    return int(hwnd), None


def _blit(hwnd: int, image, origin_x: int, origin_y: int) -> tuple[bool, str | None]:
    import ctypes

    wt = overlay_wintypes()

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    ULW_ALPHA = 0x00000002
    AC_SRC_OVER = 0x00
    AC_SRC_ALPHA = 0x01
    HWND_TOPMOST = -1
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_NOOWNERZORDER = 0x0200
    BI_RGB = 0

    image = image.convert("RGBA")
    width, height = image.size
    bgra = image.tobytes("raw", "BGRA")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_byte),
            ("BlendFlags", ctypes.c_byte),
            ("SourceConstantAlpha", ctypes.c_byte),
            ("AlphaFormat", ctypes.c_byte),
        ]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        return False, _win_error("GetDC failed")
    bits = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbm or not bits:
        user32.ReleaseDC(None, hdc_screen)
        return False, _win_error("CreateDIBSection failed")
    ctypes.memmove(bits, bgra, len(bgra))

    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    if not hdc_mem:
        gdi32.DeleteObject(hbm)
        user32.ReleaseDC(None, hdc_screen)
        return False, _win_error("CreateCompatibleDC failed")
    old = gdi32.SelectObject(hdc_mem, hbm)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    pt_dst = POINT(int(origin_x), int(origin_y))
    size = SIZE(width, height)
    pt_src = POINT(0, 0)

    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        int(origin_x),
        int(origin_y),
        width,
        height,
        SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER,
    )
    user32.UpdateLayeredWindow.argtypes = [
        wt.HWND,
        wt.HDC,
        ctypes.POINTER(POINT),
        ctypes.POINTER(SIZE),
        wt.HDC,
        ctypes.POINTER(POINT),
        wt.DWORD,
        ctypes.POINTER(BLENDFUNCTION),
        wt.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wt.BOOL
    ok = bool(
        user32.UpdateLayeredWindow(
            hwnd,
            hdc_screen,
            ctypes.byref(pt_dst),
            ctypes.byref(size),
            hdc_mem,
            ctypes.byref(pt_src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )
    )
    blit_err = None if ok else _win_error("UpdateLayeredWindow failed")
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteDC(hdc_mem)
    gdi32.DeleteObject(hbm)
    user32.ReleaseDC(None, hdc_screen)
    return ok, blit_err


_wndproc_ref = None
