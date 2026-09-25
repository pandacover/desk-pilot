"""Reuse one click-through, topmost layered overlay for the sketch highlight.

Thread rule: the overlay HWND is owned by one thread for its lifetime.
When a Tk pump is installed (Desk Pilot GUI), that is the UI main thread.
CreateWindowEx, UpdateLayeredWindow, ShowWindow, and DestroyWindow run only
on that thread via ``call_on_overlay_thread``. The agent worker must never
blit a HWND created on the Test-sketch thread or the UI thread.

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

_singleton: HighlightOverlay | None = None
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
