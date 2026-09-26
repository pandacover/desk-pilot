"""Start/stop the FastVLM sidecar subprocess. UI process never imports torch."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Callable

from desk_pilot.app.config import cache_dir
from desk_pilot.vision import DEFAULT_HOST, DEFAULT_PORT
from desk_pilot.vision.client import SCENE_INFER_TIMEOUT, SceneClient, StubSceneClient, default_sidecar_url

LogFn = Callable[[str, str], None]


def env_fastvlm_disabled() -> bool:
    text = (os.environ.get("DESK_PILOT_FASTVLM") or "").strip().lower()
    return text in {"0", "false", "no", "off"}


def env_fastvlm_stub() -> bool:
    text = (os.environ.get("DESK_PILOT_FASTVLM_STUB") or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


class SidecarManager:
    """Spawn `python -m desk_pilot.vision.sidecar` after the UI window is shown."""

    def __init__(
        self,
        *,
        url: str | None = None,
        stub: bool = False,
        enabled: bool = True,
        on_log: LogFn | None = None,
    ) -> None:
        self.url = (url or default_sidecar_url()).rstrip("/")
        self.stub = bool(stub) or env_fastvlm_stub()
        self.enabled = bool(enabled) and not env_fastvlm_disabled()
        self.on_log = on_log
        self.process: subprocess.Popen[str] | None = None
        self.client: SceneClient | StubSceneClient | None = None
        self._owned = False
        self._log_file: Any = None
        self._last_health: dict[str, Any] = {"status": "error", "error": "not started"}

    def start(self) -> None:
        if not self.enabled:
            self.client = None
            self._last_health = {"status": "error", "mode": "off", "error": "FastVLM disabled"}
            self._log("info", "FastVLM off — observe is UIA only.")
            return
        self.client = SceneClient(self.url, timeout=SCENE_INFER_TIMEOUT)
        existing = self.client.health()
        if existing.get("status") in {"loading", "ready"}:
            self._last_health = existing
            self._owned = False
            self._log("info", f"Reusing FastVLM sidecar at {self.url} ({existing.get('status')}).")
            return
        cmd = [
            sys.executable,
            "-m",
            "desk_pilot.vision.sidecar",
            "--host",
            DEFAULT_HOST,
            "--port",
            str(_port_from_url(self.url) or DEFAULT_PORT),
        ]
        if self.stub:
            cmd.append("--stub")
        log_path = cache_dir() / "fastvlm-sidecar.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "ab")  # noqa: SIM115 — kept for process lifetime
        self._log_file = log_file
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            log_file.close()
            self._last_health = {"status": "error", "error": f"could not start sidecar: {exc}"}
            self._log("error", self._last_health["error"])
            return
        self._owned = True
        self._log(
            "info",
            "Starting FastVLM sidecar in the background"
            + (" (stub)." if self.stub else " (first model download can take a while)."),
        )
        # HTTP may not bind on the first poll; keep status at loading until /health answers.
        self._last_health = {
            "status": "loading",
            "mode": "stub" if self.stub else "fastvlm",
            "note": "sidecar starting",
        }

    def poll(self) -> dict[str, Any]:
        if not self.enabled or self.client is None:
            self._last_health = {"status": "error", "mode": "off", "error": "FastVLM disabled"}
            return self._last_health
        health = self.client.health()
        if (
            health.get("status") == "error"
            and self._owned
            and self.process is not None
            and self.process.poll() is None
            and "unreachable" in str(health.get("error") or "")
        ):
            health = {
                "status": "loading",
                "mode": "stub" if self.stub else "fastvlm",
                "note": "sidecar starting",
            }
        prev = self._last_health.get("status")
        self._last_health = health
        if health.get("status") == "ready" and prev != "ready":
            mode = health.get("mode") or "fastvlm"
            device = health.get("device") or ""
            extra = f" ({device})" if device else ""
            if mode == "stub":
                self._log("info", "Vision stub ready (install requirements-vision.txt for FastVLM-0.5B).")
            else:
                self._log("info", f"Vision model ready{extra}.")
        if health.get("status") == "error" and prev != "error":
            self._log("error", health.get("error") or "FastVLM sidecar error")
        return health

    def is_ready(self) -> bool:
        return (self._last_health or {}).get("status") == "ready"

    def status_label(self) -> str:
        if not self.enabled:
            return "Vision off"
        health = self._last_health or {}
        status = health.get("status") or "error"
        if status == "loading":
            device = health.get("device") or ""
            if device == "cpu":
                return "Loading vision model (CPU, first load is slow)…"
            return "Loading vision model…"
        if status == "ready":
            if (health.get("mode") or "") == "stub":
                return "Vision stub"
            device = health.get("device") or ""
            return f"Vision ready{f' ({device})' if device else ''}"
        err = str(health.get("error") or "vision error")
        from desk_pilot.vision import short_health_error

        short = short_health_error(err)
        if short == "vision error":
            return "Vision error"
        return f"Vision: {short}"

    def scene_client(self) -> SceneClient | StubSceneClient | None:
        if not self.enabled:
            return None
        return self.client

    def stop(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        if not self._owned or self.process is None:
            return
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def _log(self, kind: str, message: str) -> None:
        if self.on_log:
            self.on_log(kind, message)


def _port_from_url(url: str) -> int | None:
    from urllib.parse import urlparse

    try:
        port = urlparse(url).port
    except ValueError:
        return None
    return int(port) if port else None
