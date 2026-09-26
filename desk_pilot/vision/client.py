"""HTTP client for the FastVLM sidecar. No torch import."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from desk_pilot.vision import DEFAULT_HOST, DEFAULT_PORT
from desk_pilot.vision.scene import empty_scene, normalize_scene

# Agent-loop /scene wait. CPU FastVLM can exceed this; fail-open to UIA rather than hang.
SCENE_INFER_TIMEOUT = 25.0


def default_sidecar_url() -> str:
    import os

    return (os.environ.get("DESK_PILOT_FASTVLM_URL") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}").rstrip("/")


class SceneClient:
    """POST /scene and GET /health against a local sidecar."""

    def __init__(self, base_url: str | None = None, *, timeout: float = SCENE_INFER_TIMEOUT) -> None:
        self.base_url = (base_url or default_sidecar_url()).rstrip("/")
        self.timeout = timeout
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=2.0))

    def close(self) -> None:
        self._http.close()

    def health(self) -> dict[str, Any]:
        try:
            response = self._http.get(urljoin(self.base_url + "/", "health"), timeout=1.5)
        except httpx.HTTPError as exc:
            return {"status": "error", "error": f"sidecar unreachable: {exc}"}
        if response.status_code >= 400:
            return {"status": "error", "error": f"health HTTP {response.status_code}"}
        try:
            data = response.json()
        except ValueError:
            return {"status": "error", "error": "health returned non-JSON"}
        if not isinstance(data, dict):
            return {"status": "error", "error": "health returned non-object"}
        status = str(data.get("status") or "error")
        if status not in {"loading", "ready", "error"}:
            status = "error"
        data["status"] = status
        return data

    def is_ready(self) -> bool:
        return self.health().get("status") == "ready"

    def infer_scene(
        self,
        *,
        path: str | None = None,
        image_bytes: bytes | None = None,
        window: str = "",
        region: dict[str, int] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"window": window or "", "region": region or {"x": 0, "y": 0, "w": 1, "h": 1}}
        if path:
            body["path"] = path
        if image_bytes:
            import base64

            body["image_b64"] = base64.b64encode(image_bytes).decode("ascii")
        read_timeout = self.timeout if timeout is None else float(timeout)
        try:
            response = self._http.post(
                urljoin(self.base_url + "/", "scene"),
                json=body,
                timeout=httpx.Timeout(read_timeout, connect=2.0),
            )
        except httpx.HTTPError as exc:
            return empty_scene(window=window, region=region, note=f"sidecar /scene failed: {exc}")
        if response.status_code >= 400:
            return empty_scene(
                window=window,
                region=region,
                note=f"sidecar /scene HTTP {response.status_code}: {response.text[:120]}",
            )
        try:
            data = response.json()
        except ValueError:
            return empty_scene(window=window, region=region, note="sidecar /scene returned non-JSON")
        if not isinstance(data, dict):
            return empty_scene(window=window, region=region, note="sidecar /scene returned non-object")
        return normalize_scene(data, window=window, region=region)


class StubSceneClient:
    """In-process scene for tests and when FastVLM is off. Never talks HTTP."""

    def __init__(self, scene: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._scene = scene

    def close(self) -> None:
        return

    def health(self) -> dict[str, Any]:
        return {"status": "ready", "mode": "stub", "device": None}

    def is_ready(self) -> bool:
        return True

    def infer_scene(
        self,
        *,
        path: str | None = None,
        image_bytes: bytes | None = None,
        window: str = "",
        region: dict[str, int] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        payload = {
            "path": path,
            "has_bytes": bool(image_bytes),
            "window": window,
            "region": region,
        }
        self.calls.append(payload)
        if self._scene:
            return normalize_scene(self._scene, window=window, region=region)
        return empty_scene(window=window, region=region, note="stub: FastVLM not loaded")
