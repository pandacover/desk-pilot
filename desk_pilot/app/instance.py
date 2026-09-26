"""Single-instance UI + sidecar PID hygiene."""

from __future__ import annotations

import os
from pathlib import Path

from desk_pilot.app.config import cache_dir

UI_PID_NAME = "desk-pilot.pid"
SIDECAR_PID_NAME = "fastvlm-sidecar.pid"


def ui_pid_path() -> Path:
    return cache_dir() / UI_PID_NAME


def sidecar_pid_path() -> Path:
    return cache_dir() / SIDECAR_PID_NAME


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip().split()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw[0])
    except ValueError:
        return None


def write_pid(path: Path, pid: int, *, tag: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{int(pid)} {tag}".strip() + "\n"
    path.write_text(line, encoding="utf-8")


def acquire_ui_lock() -> tuple[bool, int | None]:
    """Claim the UI pidfile. False + other pid if another Desk Pilot UI is alive."""
    path = ui_pid_path()
    other = read_pid(path)
    if other and other != os.getpid() and pid_is_running(other):
        return False, other
    write_pid(path, os.getpid(), tag="desk_pilot.ui")
    return True, None


def release_ui_lock() -> None:
    path = ui_pid_path()
    owned = read_pid(path)
    if owned == os.getpid():
        try:
            path.unlink()
        except OSError:
            pass


def write_sidecar_pid(pid: int) -> None:
    write_pid(sidecar_pid_path(), pid, tag="desk_pilot.vision.sidecar")


def clear_sidecar_pid(pid: int | None = None) -> None:
    path = sidecar_pid_path()
    owned = read_pid(path)
    if pid is None or owned == pid:
        try:
            path.unlink()
        except OSError:
            pass


def kill_stale_sidecar() -> int | None:
    """Terminate a previous sidecar PID if it is still alive. Returns killed pid."""
    path = sidecar_pid_path()
    pid = read_pid(path)
    if not pid or pid == os.getpid() or not pid_is_running(pid):
        if pid and not pid_is_running(pid):
            clear_sidecar_pid(pid)
        return None
    _kill_pid(pid)
    clear_sidecar_pid(pid)
    return pid


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        import subprocess

        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/F"],
            capture_output=True,
            check=False,
        )
        return
    import signal

    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return
