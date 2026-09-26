"""FastVLM sidecar: tiny localhost HTTP, loads the model in a background thread.

Run:  python -m desk_pilot.vision.sidecar
      python -m desk_pilot.vision.sidecar --stub   # no torch; empty scenes for dry-run/CI

GET  /health  → {"status":"loading"|"ready"|"error", "mode":"fastvlm"|"stub", "device":...}
POST /scene   JSON {path|image_b64, window, region} → compact scene JSON
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any

from desk_pilot.vision import DEFAULT_HOST, DEFAULT_MODEL_ID, DEFAULT_PORT
from desk_pilot.vision.scene import SCENE_PROMPT, empty_scene, parse_scene_text

IMAGE_TOKEN_INDEX = -200

_state_lock = threading.Lock()
_infer_lock = threading.Lock()
_status = "loading"
_mode = "fastvlm"
_device: str | None = None
_error: str | None = None
_note: str | None = None
_model = None
_tokenizer = None


def health_payload() -> dict[str, Any]:
    with _state_lock:
        payload: dict[str, Any] = {
            "status": _status,
            "mode": _mode,
            "device": _device,
            "model": DEFAULT_MODEL_ID if _mode == "fastvlm" else None,
        }
        if _error:
            payload["error"] = _error
        if _note:
            payload["note"] = _note
        return payload


def set_state(
    *,
    status: str | None = None,
    mode: str | None = None,
    device: str | None = None,
    error: str | None = None,
    note: str | None = None,
) -> None:
    global _status, _mode, _device, _error, _note
    with _state_lock:
        if status is not None:
            _status = status
        if mode is not None:
            _mode = mode
        if device is not None:
            _device = device
        if error is not None:
            _error = error
        if note is not None:
            _note = note


def load_model() -> None:
    """Import torch only in this sidecar process, after HTTP is already serving /health."""
    global _model, _tokenizer
    try:
        import torch
        from PIL import Image  # noqa: F401 — fail fast if pillow missing
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:  # noqa: BLE001
        set_state(
            status="error",
            mode="fastvlm",
            error=(
                f"FastVLM deps missing ({type(exc).__name__}: {exc}). "
                "Install with: pip install -r requirements-vision.txt"
            ),
            note="UI stays up; uncheck FastVLM in Settings or install vision deps.",
        )
        return
    try:
        cuda = bool(torch.cuda.is_available())
        device = "cuda" if cuda else "cpu"
        dtype = torch.float16 if cuda else torch.float32
        set_state(
            status="loading",
            mode="fastvlm",
            device=device,
            note=None if cuda else "CPU is OK; first load is slow.",
        )
        tokenizer = AutoTokenizer.from_pretrained(DEFAULT_MODEL_ID, trust_remote_code=True)
        kwargs: dict[str, Any] = {
            "torch_dtype": dtype,
            "trust_remote_code": True,
        }
        if cuda:
            kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(DEFAULT_MODEL_ID, **kwargs)
        if not cuda:
            model = model.to("cpu")
        model.eval()
        _tokenizer = tokenizer
        _model = model
        set_state(
            status="ready",
            mode="fastvlm",
            device=device,
            error="",
            note=None if cuda else "CPU inference; first load is slow.",
        )
    except Exception as exc:  # noqa: BLE001
        set_state(
            status="error",
            mode="fastvlm",
            error=f"FastVLM load failed: {type(exc).__name__}: {exc}",
            note=traceback.format_exc()[-400:],
        )


def enter_stub(reason: str = "stub mode") -> None:
    set_state(status="ready", mode="stub", device=None, error="", note=reason)


def infer_scene(path: str | None, image_bytes: bytes | None, window: str, region: dict[str, int]) -> dict[str, Any]:
    with _state_lock:
        mode = _mode
        status = _status
        model = _model
        tokenizer = _tokenizer
    if status != "ready":
        return empty_scene(window=window, region=region, note=f"sidecar not ready ({status})")
    if mode == "stub" or model is None or tokenizer is None:
        return empty_scene(window=window, region=region, note="stub: FastVLM not loaded")
    image = _open_image(path, image_bytes)
    if image is None:
        return empty_scene(window=window, region=region, note="could not open screenshot")
    prompt = (
        f"{SCENE_PROMPT} region={json.dumps(region, separators=(',', ':'))} "
        f"window={window or '?'}"
    )
    try:
        text = _generate(model, tokenizer, image, prompt)
    except Exception as exc:  # noqa: BLE001
        return empty_scene(window=window, region=region, note=f"FastVLM generate failed: {exc}")
    return parse_scene_text(text, window=window, region=region)


def _open_image(path: str | None, image_bytes: bytes | None):
    from PIL import Image

    if image_bytes:
        return Image.open(BytesIO(image_bytes)).convert("RGB")
    if path:
        file = Path(path)
        if file.is_file():
            return Image.open(file).convert("RGB")
    return None


def _generate(model: Any, tokenizer: Any, image: Any, prompt: str) -> str:
    import torch

    messages = [{"role": "user", "content": f"<image>\n{prompt}"}]
    rendered = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
    if "<image>" not in rendered:
        rendered = f"<image>\n{prompt}"
    pre, post = rendered.split("<image>", 1)
    pre_ids = tokenizer(pre, return_tensors="pt", add_special_tokens=False).input_ids
    post_ids = tokenizer(post, return_tensors="pt", add_special_tokens=False).input_ids
    img_tok = torch.tensor([[IMAGE_TOKEN_INDEX]], dtype=pre_ids.dtype)
    input_ids = torch.cat([pre_ids, img_tok, post_ids], dim=1).to(model.device)
    attention_mask = torch.ones_like(input_ids, device=model.device)
    px = model.get_vision_tower().image_processor(images=image, return_tensors="pt")["pixel_values"]
    px = px.to(model.device, dtype=model.dtype)
    with torch.no_grad():
        out = model.generate(
            inputs=input_ids,
            attention_mask=attention_mask,
            images=px,
            max_new_tokens=512,
            do_sample=False,
        )
    decoded = tokenizer.decode(out[0], skip_special_tokens=True)
    return str(decoded or "")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("fastvlm-sidecar: " + (fmt % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/health":
            self._send(404, {"status": "error", "error": "not found"})
            return
        self._send(200, health_payload())

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/scene":
            self._send(404, {"status": "error", "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(max(0, min(length, 8_000_000))) if length else b""
        path, image_bytes, window, region = _parse_scene_body(raw, self.headers.get("Content-Type") or "")
        with _infer_lock:
            scene = infer_scene(path, image_bytes, window, region)
        self._send(200, scene)

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _parse_scene_body(raw: bytes, content_type: str) -> tuple[str | None, bytes | None, str, dict[str, int]]:
    window = ""
    region = {"x": 0, "y": 0, "w": 1, "h": 1}
    path = None
    image_bytes = None
    if "json" in (content_type or "").lower() or (raw[:1] == b"{"):
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            window = str(data.get("window") or "")
            if isinstance(data.get("region"), dict):
                try:
                    region = {
                        "x": int(data["region"].get("x") or 0),
                        "y": int(data["region"].get("y") or 0),
                        "w": max(1, int(data["region"].get("w") or data["region"].get("width") or 1)),
                        "h": max(1, int(data["region"].get("h") or data["region"].get("height") or 1)),
                    }
                except (TypeError, ValueError):
                    pass
            path = str(data.get("path") or "") or None
            b64 = data.get("image_b64")
            if isinstance(b64, str) and b64.strip():
                try:
                    image_bytes = base64.b64decode(b64)
                except Exception:
                    image_bytes = None
            return path, image_bytes, window, region
    if raw:
        image_bytes = raw
    return path, image_bytes, window, region


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    actual = httpd.server_address[1]
    sys.stdout.write(f"FASTVLM_SIDECAR http://{host}:{actual}\n")
    sys.stdout.flush()
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Desk Pilot FastVLM sidecar")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--stub",
        action="store_true",
        help="Do not load PyTorch; /scene returns an empty stub scene (dry-run/CI).",
    )
    args = parser.parse_args(argv)
    stub = bool(args.stub) or os.environ.get("DESK_PILOT_FASTVLM_STUB", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if stub:
        enter_stub("stub: FastVLM weights not loaded")
    else:
        set_state(status="loading", mode="fastvlm", note="Loading vision model…")
        threading.Thread(target=load_model, name="fastvlm-load", daemon=True).start()
    try:
        serve(args.host, int(args.port))
    except OSError as exc:
        sys.stderr.write(f"fastvlm-sidecar bind failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
