"""Reuse one click-through, topmost layered overlay for the sketch highlight."""

from __future__ import annotations

import sys
import time
from typing import Sequence

HIGHLIGHT_SECONDS = 0.4

_singleton: HighlightOverlay | None = None


class HighlightOverlay:
    """Win32 WS_EX_LAYERED + TRANSPARENT + NOACTIVATE host. No-op off Windows."""

    def __init__(self) -> None:
        self._hwnd = 0
        self._class_atom = 0

    def show(self, rect: Sequence[float] | None) -> bool:
        """Paint the sketch and leave it up until hide(). Click-through, no focus steal."""
        if sys.platform != "win32" or not rect:
            return False
        from desk_pilot.desktop.sketch import normalize_rect, render_sketch

        left, top, right, bottom = normalize_rect(rect)
        if (right - left) < 1 and (bottom - top) < 1:
            return False
        image, origin_x, origin_y = render_sketch(rect)
        if image.width < 2 or image.height < 2:
            return False
        if not self._ensure_window():
            return False
        return bool(_blit(self._hwnd, image, origin_x, origin_y))

    def flash(self, rect: Sequence[float] | None, *, duration: float = HIGHLIGHT_SECONDS) -> None:
        """Brief show+hide (unused by auto-act; kept for tests)."""
        if not self.show(rect):
            return
        wait = max(0.0, min(1.0, float(duration)))
        if wait:
            time.sleep(wait)
        self.hide()

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

    def _ensure_window(self) -> bool:
        if sys.platform != "win32":
            return False
        if self._hwnd:
            return True
        try:
            self._hwnd = _create_overlay_hwnd()
        except Exception:
            self._hwnd = 0
        return bool(self._hwnd)


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


def _create_overlay_hwnd() -> int:
    import ctypes
    from ctypes import wintypes

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

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

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
            return 0

    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
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
    return int(hwnd or 0)


def _blit(hwnd: int, image, origin_x: int, origin_y: int) -> bool:
    import ctypes
    from ctypes import wintypes

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
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

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
        return False
    bits = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbm or not bits:
        user32.ReleaseDC(None, hdc_screen)
        return False
    ctypes.memmove(bits, bgra, len(bgra))

    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
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
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(POINT),
        ctypes.POINTER(SIZE),
        wintypes.HDC,
        ctypes.POINTER(POINT),
        wintypes.DWORD,
        ctypes.POINTER(BLENDFUNCTION),
        wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
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
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteDC(hdc_mem)
    gdi32.DeleteObject(hbm)
    user32.ReleaseDC(None, hdc_screen)
    return ok


_wndproc_ref = None
