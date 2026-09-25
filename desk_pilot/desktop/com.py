"""Initialize COM (STA) on every thread that talks to UI Automation.

Windows UI Automation is apartment-threaded. CustomTkinter runs the agent on a
worker thread, which does not inherit CoInitialize from the GUI thread — that is
the classic "CoInitialize has not been called" failure. Call ``ensure_com`` (or
``bind_uia_to_this_thread``) before any ``uiautomation`` use, and recreate the
library's process-wide COM singleton if this thread did not create it.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

_tls = threading.local()

COINIT_APARTMENTTHREADED = 0x2
S_OK = 0
S_FALSE = 1  # already initialized with the same mode
RPC_E_CHANGED_MODE = 0x80010106  # already initialized as MTA
CO_E_NOTINITIALIZED = 0x800401F0

_COM_NEEDLES = (
    "coinitialize has not been called",
    "coinitializeex has not been called",
    "co_e_notinitialized",
    "rpc_e_changed_mode",
    "rpc_e_wrong_thread",
    "marshalled for a different thread",
    "0x800401f0",
    "0x8001010e",
    "0x80010106",
)


def looks_like_com_error(value: Any) -> bool:
    text = str(value or "").lower()
    if not text:
        return False
    return any(needle in text for needle in _COM_NEEDLES)


def ensure_com(*, force: bool = False) -> dict[str, Any]:
    """Initialize COM STA on this thread. Safe to call repeatedly. No-op off Windows."""
    if sys.platform != "win32":
        return {"ok": True, "skipped": True, "platform": sys.platform}
    if not force and getattr(_tls, "ready", False):
        return {"ok": True, "already": True, "hr": int(getattr(_tls, "hr", 0))}
    hr, owned, error = _coinitialize_sta()
    ready = hr in (S_OK, S_FALSE) or (hr & 0xFFFFFFFF) == RPC_E_CHANGED_MODE
    _tls.ready = ready
    _tls.owned = bool(owned and hr == S_OK)
    _tls.hr = hr
    if ready:
        return {"ok": True, "hr": hr, "owned": bool(_tls.owned)}
    return {
        "ok": False,
        "hr": hr,
        "error": error or f"CoInitializeEx failed HRESULT=0x{hr & 0xFFFFFFFF:08X}",
    }


def uninitialize_com() -> None:
    """Undo COM init only if this thread called CoInitializeEx successfully."""
    if sys.platform != "win32":
        return
    owned = bool(getattr(_tls, "owned", False))
    _tls.ready = False
    _tls.owned = False
    if owned:
        _couninitialize()


def bind_uia_to_this_thread(auto_module: Any) -> dict[str, Any]:
    """CoInitialize this thread and point uiautomation's COM singleton at it."""
    init = ensure_com()
    if not init.get("ok"):
        return init
    ident = threading.get_ident()
    for module in _uia_modules(auto_module):
        client_cls = getattr(module, "_AutomationClient", None)
        owner = getattr(module, "_desk_pilot_com_thread", None)
        if client_cls is None:
            continue
        if owner != ident:
            try:
                client_cls._instance = None
            except Exception:
                pass
            try:
                setattr(module, "_desk_pilot_com_thread", ident)
            except Exception:
                pass
    return init


class com_thread:
    """Context manager: STA COM for the life of a worker/CLI run."""

    def __enter__(self) -> "com_thread":
        ensure_com()
        return self

    def __exit__(self, *_exc: object) -> None:
        uninitialize_com()


def _uia_modules(auto_module: Any) -> list[Any]:
    modules = [auto_module]
    inner = getattr(auto_module, "uiautomation", None)
    if inner is not None and inner is not auto_module:
        modules.append(inner)
    return modules


def _coinitialize_sta() -> tuple[int, bool, str]:
    try:
        import comtypes

        try:
            comtypes.CoInitialize()
            return S_OK, True, ""
        except OSError as exc:
            # Already initialized (same or different apartment).
            hr = int(getattr(exc, "winerror", 0) or 0) & 0xFFFFFFFF
            if hr in (S_FALSE, RPC_E_CHANGED_MODE, 0):
                return (hr or S_FALSE), False, ""
            return hr, False, str(exc)
        except Exception as exc:  # noqa: BLE001
            if looks_like_com_error(exc):
                return RPC_E_CHANGED_MODE, False, str(exc)
            return -1, False, str(exc)
    except ImportError:
        pass
    return _coinitialize_sta_ctypes()


def _coinitialize_sta_ctypes() -> tuple[int, bool, str]:
    import ctypes

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_long
    hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
    unsigned = hr & 0xFFFFFFFF
    if hr in (S_OK, S_FALSE) or unsigned == RPC_E_CHANGED_MODE:
        return hr, hr == S_OK, ""
    return hr, False, f"CoInitializeEx HRESULT=0x{unsigned:08X}"


def _couninitialize() -> None:
    try:
        import comtypes

        comtypes.CoUninitialize()
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass
