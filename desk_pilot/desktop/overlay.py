"""Reuse one click-through, topmost layered overlay for the sketch highlight.

Thread rule: one owner thread owns the overlay HWND for its lifetime.
When a Tk pump is installed (Desk Pilot GUI), that is the UI main thread.
CreateWindowEx, UpdateLayeredWindow, ShowWindow, and DestroyWindow run only
on that thread via ``call_on_overlay_thread``. Test sketch, guide, hide, and
close all go through that owner. Never blit from the agent worker, and never
reuse a HWND created on another thread.

If UpdateLayeredWindow (or SetWindowPos) returns ERROR_INVALID_WINDOW_HANDLE
(1400), destroy/forget the handle on the owner thread, CreateWindowEx again,
and blit once more. A failed blit never leaves a stale HWND on the singleton.

If no pump is set (CLI / unit tests), the caller thread owns the window and
must create and blit on that same thread.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, Sequence, TypeVar

HIGHLIGHT_SECONDS = 0.4
MARSHAL_TIMEOUT = 8.0
ERROR_INVALID_WINDOW_HANDLE = 1400

_singleton: HighlightOverlay | None = None
_singleton_lock = threading.Lock()
_pump: Callable[[Callable[[], None]], None] | None = None
_owner_tid: int | None = None

T = TypeVar("T")
OverlayJob = Callable[[], None]


def set_overlay_pump(pump: Callable[[OverlayJob], None] | None) -> None:
    """Install how to run overlay Win32 on the HWND owner thread.

    ``pump(fn)`` must arrange for ``fn`` to run on the UI thread. Desk Pilot
    posts ``fn`` on a queue and wakes Tk with ``after(0, …)``. Pass ``None``
    to clear (CLI / tests). The installing thread becomes the owner.
    """
    global _pump, _owner_tid
    _pump = pump
    _owner_tid = threading.get_ident() if pump is not None else None


def overlay_owner_thread_id() -> int | None:
    return _owner_tid


def overlay_thread_note(*, hwnd: int = 0, hwnd_tid: int | None = None) -> str:
    """Caller vs owner vs HWND-create thread ids for SKETCH failed lines."""
    return (
        f"tid={threading.get_ident()} owner={_owner_tid} "
        f"hwnd_tid={hwnd_tid} hwnd={hwnd}"
    )


def is_invalid_hwnd_error(message: str | None, code: int | None = None) -> bool:
    """True for Win32 ERROR_INVALID_WINDOW_HANDLE (1400)."""
    if code == ERROR_INVALID_WINDOW_HANDLE:
        return True
    text = message or ""
    if f"GetLastError={ERROR_INVALID_WINDOW_HANDLE}" in text:
        return True
    return "invalid window handle" in text.lower()


def call_on_overlay_thread(fn: Callable[[], T], *, timeout: float = MARSHAL_TIMEOUT) -> T:
    """Run ``fn`` on the HWND owner thread; inline if we are already there or have no pump."""
    if _pump is None or threading.get_ident() == _owner_tid:
        return fn()
    holder: list[tuple[str, Any]] = []
    done = threading.Event()

    def job() -> None:
        try:
            holder.append(("ok", fn()))
        except BaseException as exc:  # noqa: BLE001 — marshal to waiter
            holder.append(("err", exc))
        finally:
            done.set()

    _pump(job)
    if not done.wait(timeout):
        raise TimeoutError(
            f"overlay marshal to UI thread timed out after {timeout}s "
            f"({overlay_thread_note()})"
        )
    if not holder:
        raise RuntimeError(f"overlay marshal returned no result ({overlay_thread_note()})")
    kind, value = holder[0]
    if kind == "err":
        raise value
    return value


class HighlightOverlay:
    """Win32 WS_EX_LAYERED + TRANSPARENT + NOACTIVATE host. No-op off Windows."""

    def __init__(self) -> None:
        self._hwnd = 0
        self._hwnd_tid: int | None = None
        self._class_atom = 0

    def show(self, rect: Sequence[float] | None) -> dict[str, Any]:
        """Paint the sketch and leave it up until hide(). Click-through, no focus steal."""
        from desk_pilot.desktop.rects import SKIP_NO_RECT, rect_skip_reason, sketchable_rect
        from desk_pilot.desktop.sketch import render_sketch

        skip = rect_skip_reason(rect)
        box = sketchable_rect(rect)
        if not box:
            return {"ok": False, "skipped": True, "error": skip or SKIP_NO_RECT, "rect": None}
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
        try:
            return call_on_overlay_thread(
                lambda: self._show_win32(box, image, origin_x, origin_y)
            )
        except Exception as exc:  # noqa: BLE001 — marshal/Win32 must reach the log
            return {
                "ok": False,
                "skipped": False,
                "recreated": False,
                "error": f"{type(exc).__name__}: {exc} {overlay_thread_note(hwnd=self._hwnd, hwnd_tid=self._hwnd_tid)}",
                "rect": box,
            }

    def _show_win32(self, box: list[int], image: Any, origin_x: int, origin_y: int) -> dict[str, Any]:
        """Owner-thread paint. Recreates the HWND once after Win32 error 1400."""
        hwnd, err = self._ensure_window()
        note = overlay_thread_note(hwnd=hwnd, hwnd_tid=self._hwnd_tid)
        if not hwnd:
            return {
                "ok": False,
                "skipped": False,
                "recreated": False,
                "error": f"{err or 'CreateWindowExW failed'} {note}",
                "rect": box,
            }
        ok, blit_err = self._paint_once(hwnd, image, origin_x, origin_y)
        if ok:
            return {"ok": True, "skipped": False, "recreated": False, "error": None, "rect": box}

        first_err = blit_err or "UpdateLayeredWindow failed"
        self._destroy_and_forget()
        if not is_invalid_hwnd_error(first_err):
            note = overlay_thread_note(hwnd=0, hwnd_tid=None)
            return {
                "ok": False,
                "skipped": False,
                "recreated": False,
                "error": f"{first_err} {note}",
                "rect": box,
            }

        hwnd, err = self._ensure_window()
        note = overlay_thread_note(hwnd=hwnd, hwnd_tid=self._hwnd_tid)
        if not hwnd:
            return {
                "ok": False,
                "skipped": False,
                "recreated": False,
                "error": f"{first_err} (hwnd recreate failed: {err}) {note}",
                "rect": box,
            }
        ok, blit_err = self._paint_once(hwnd, image, origin_x, origin_y)
        if ok:
            return {"ok": True, "skipped": False, "recreated": True, "error": None, "rect": box}
        still = blit_err or first_err
        self._destroy_and_forget()
        note = overlay_thread_note(hwnd=0, hwnd_tid=None)
        return {
            "ok": False,
            "skipped": False,
            "recreated": True,
            "error": f"{still} (hwnd recreated, still failed) {note}",
            "rect": box,
        }

    def _paint_once(self, hwnd: int, image: Any, origin_x: int, origin_y: int) -> tuple[bool, str | None]:
        try:
            return self._paint_hwnd(hwnd, image, origin_x, origin_y)
        except Exception as exc:  # noqa: BLE001 — 64-bit handle convert errors must surface
            return False, f"{type(exc).__name__}: {exc}"

    def _paint_hwnd(self, hwnd: int, image: Any, origin_x: int, origin_y: int) -> tuple[bool, str | None]:
        return _blit(hwnd, image, origin_x, origin_y)

    def _create_hwnd(self) -> tuple[int, str | None]:
        if sys.platform != "win32":
            return 0, "overlay only on Windows"
        return _create_overlay_hwnd()

    def _destroy_hwnd(self, hwnd: int) -> None:
        if sys.platform != "win32" or not hwnd:
            return
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        apply_overlay_argtypes(user32=user32)
        user32.DestroyWindow(hwnd)

    def _forget_hwnd(self) -> None:
        self._hwnd = 0
        self._hwnd_tid = None

    def _destroy_and_forget(self) -> None:
        hwnd = self._hwnd
        self._forget_hwnd()
        if not hwnd:
            return
        try:
            self._destroy_hwnd(hwnd)
        except Exception:
            return

    def flash(self, rect: Sequence[float] | None, *, duration: float = HIGHLIGHT_SECONDS) -> dict[str, Any]:
        """Brief show+hide (unused by auto-act; kept for tests). Sleep stays on the caller thread."""
        result = self.show(rect)
        if not result.get("ok"):
            return result
        wait = max(0.0, min(1.0, float(duration)))
        if wait:
            time.sleep(wait)
        self.hide()
        return result

    def hide(self) -> None:
        if sys.platform != "win32":
            return
        try:
            call_on_overlay_thread(self._hide_win32)
        except Exception:
            return

    def _hide_win32(self) -> None:
        if not self._hwnd:
            return
        try:
            self._hide_hwnd(self._hwnd)
            if not self._hwnd_is_alive(self._hwnd):
                self._forget_hwnd()
        except Exception:
            if not self._hwnd_is_alive(self._hwnd):
                self._forget_hwnd()

    def _hide_hwnd(self, hwnd: int) -> None:
        if sys.platform != "win32" or not hwnd:
            return
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        apply_overlay_argtypes(user32=user32)
        user32.ShowWindow(hwnd, 0)  # SW_HIDE

    def close(self) -> None:
        if sys.platform != "win32" and not self._hwnd:
            self._forget_hwnd()
            return
        try:
            call_on_overlay_thread(self._close_win32)
        except Exception:
            self._forget_hwnd()

    def _close_win32(self) -> None:
        self._destroy_and_forget()

    def _ensure_window(self) -> tuple[int, str | None]:
        if self._hwnd_usable():
            return self._hwnd, None
        self._forget_hwnd()
        try:
            hwnd, err = self._create_hwnd()
        except Exception as exc:  # noqa: BLE001 — Win32 class/window create must surface
            self._forget_hwnd()
            return 0, f"{type(exc).__name__}: {exc}"
        if not hwnd:
            self._forget_hwnd()
            return 0, err
        self._hwnd = hwnd
        self._hwnd_tid = threading.get_ident()
        return hwnd, None

    def _hwnd_is_alive(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        if sys.platform != "win32":
            return True
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            apply_overlay_argtypes(user32=user32)
            return bool(user32.IsWindow(hwnd))
        except Exception:
            return False

    def _hwnd_usable(self) -> bool:
        """False if missing, created on another thread, or already destroyed (ERROR 1400)."""
        if not self._hwnd:
            return False
        if self._hwnd_tid is not None and self._hwnd_tid != threading.get_ident():
            # Do not DestroyWindow from the wrong thread; drop the stale handle.
            self._forget_hwnd()
            return False
        if not self._hwnd_is_alive(self._hwnd):
            self._forget_hwnd()
            return False
        return True


def get_overlay() -> HighlightOverlay:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = HighlightOverlay()
        return _singleton


def close_overlay() -> None:
    global _singleton
    with _singleton_lock:
        overlay = _singleton
        _singleton = None
    if overlay is not None:
        overlay.close()


def overlay_api_argtypes(wintypes_mod: Any | None = None) -> dict[str, tuple[list[Any], Any]]:
    """Pointer-sized HWND/HDC/HBITMAP prototypes so x64 handles are not stuffed into c_int."""
    import ctypes

    wt = overlay_wintypes(wintypes_mod)
    hwnd, hdc, hbitmap = wt.HWND, wt.HDC, wt.HBITMAP
    hgdi = ctypes.c_void_p
    cint = ctypes.c_int
    return {
        "GetDC": ([hwnd], hdc),
        "ReleaseDC": ([hwnd, hdc], cint),
        "SetWindowPos": ([hwnd, hwnd, cint, cint, cint, cint, wt.UINT], wt.BOOL),
        "ShowWindow": ([hwnd, cint], wt.BOOL),
        "DestroyWindow": ([hwnd], wt.BOOL),
        "IsWindow": ([hwnd], wt.BOOL),
        "GetModuleHandleW": ([wt.LPCWSTR], wt.HINSTANCE),
        "CreateCompatibleDC": ([hdc], hdc),
        "SelectObject": ([hdc, hgdi], hgdi),
        "DeleteDC": ([hdc], wt.BOOL),
        "DeleteObject": ([hgdi], wt.BOOL),
        "CreateDIBSection": (
            [hdc, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), hwnd, wt.DWORD],
            hbitmap,
        ),
    }


def apply_overlay_argtypes(
    *,
    user32: Any | None = None,
    gdi32: Any | None = None,
    kernel32: Any | None = None,
    wintypes_mod: Any | None = None,
) -> None:
    mapping = {
        "GetDC": user32,
        "ReleaseDC": user32,
        "SetWindowPos": user32,
        "ShowWindow": user32,
        "DestroyWindow": user32,
        "IsWindow": user32,
        "GetModuleHandleW": kernel32,
        "CreateCompatibleDC": gdi32,
        "SelectObject": gdi32,
        "DeleteDC": gdi32,
        "DeleteObject": gdi32,
        "CreateDIBSection": gdi32,
    }
    for name, (argtypes, restype) in overlay_api_argtypes(wintypes_mod).items():
        dll = mapping.get(name)
        if dll is None:
            continue
        fn = getattr(dll, name, None)
        if fn is None:
            continue
        fn.argtypes = argtypes
        fn.restype = restype


def hwnd_topmost_handle() -> Any:
    """HWND_TOPMOST (-1) as a pointer-sized handle, not a 32-bit int."""
    wt = overlay_wintypes()
    return coerce_win_handle(wt.HWND, -1)


def coerce_win_handle(typ: Any, value: Any) -> Any:
    """Build a pointer-sized HWND/HDC/HBITMAP so x64 values never convert through c_int."""
    import ctypes

    if value is None:
        return None
    try:
        return typ(value)
    except (OverflowError, TypeError, ValueError):
        try:
            return ctypes.c_void_p(int(value))
        except (OverflowError, TypeError, ValueError):
            return ctypes.c_void_p(-1)


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


def _win_error(label: str, err: int | None = None) -> str:
    import ctypes

    if err is None:
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
    apply_overlay_argtypes(user32=user32, kernel32=kernel32)

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
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.WORD

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

    from desk_pilot.desktop.rects import MAX_ABS_COORD, MAX_EDGE, SKIP_ABSURD_RECT

    wt = overlay_wintypes()

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    apply_overlay_argtypes(user32=user32, gdi32=gdi32)

    origin_x, origin_y = int(origin_x), int(origin_y)
    if abs(origin_x) > MAX_ABS_COORD or abs(origin_y) > MAX_ABS_COORD:
        return False, SKIP_ABSURD_RECT

    ULW_ALPHA = 0x00000002
    AC_SRC_OVER = 0x00
    AC_SRC_ALPHA = 0x01
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    SWP_NOOWNERZORDER = 0x0200
    BI_RGB = 0

    image = image.convert("RGBA")
    width, height = image.size
    if width < 2 or height < 2 or width > MAX_EDGE or height > MAX_EDGE:
        return False, SKIP_ABSURD_RECT
    if abs(origin_x + width) > MAX_ABS_COORD or abs(origin_y + height) > MAX_ABS_COORD:
        return False, SKIP_ABSURD_RECT
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
    hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbm or not bits:
        user32.ReleaseDC(None, hdc_screen)
        return False, _win_error("CreateDIBSection failed")
    ctypes.memmove(bits, bgra, len(bgra))

    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    if not hdc_mem:
        gdi32.DeleteObject(coerce_win_handle(ctypes.c_void_p, hbm))
        user32.ReleaseDC(None, hdc_screen)
        return False, _win_error("CreateCompatibleDC failed")
    hbm_h = coerce_win_handle(ctypes.c_void_p, hbm)
    old = gdi32.SelectObject(hdc_mem, hbm_h)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    pt_dst = POINT(int(origin_x), int(origin_y))
    size = SIZE(width, height)
    pt_src = POINT(0, 0)

    hwnd_h = coerce_win_handle(wt.HWND, hwnd)
    pos_ok = bool(
        user32.SetWindowPos(
            hwnd_h,
            hwnd_topmost_handle(),
            int(origin_x),
            int(origin_y),
            width,
            height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER,
        )
    )
    if not pos_ok:
        pos_err = ctypes.get_last_error()
        if pos_err == ERROR_INVALID_WINDOW_HANDLE:
            gdi32.SelectObject(hdc_mem, old)
            gdi32.DeleteDC(hdc_mem)
            gdi32.DeleteObject(hbm_h)
            user32.ReleaseDC(None, hdc_screen)
            return False, _win_error("SetWindowPos failed", pos_err)
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
            hwnd_h,
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
    last_err = 0 if ok else ctypes.get_last_error()
    blit_err = None if ok else _win_error("UpdateLayeredWindow failed", last_err)
    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteDC(hdc_mem)
    gdi32.DeleteObject(hbm_h)
    user32.ReleaseDC(None, hdc_screen)
    return ok, blit_err


_wndproc_ref = None
