"""FastVLM sidecar: tiny localhost HTTP, loads the model in a background thread.

Run:  python -m desk_pilot.vision.sidecar
      python -m desk_pilot.vision.sidecar --stub   # no torch; empty scenes for dry-run/CI

GET  /health  → {"status":"loading"|"ready"|"busy"|"error", "phase":..., "mode":..., "device":...}
POST /scene   JSON {path|image_b64, window, region} → compact scene JSON
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any

from desk_pilot.vision import (
    DEFAULT_HOST,
    DEFAULT_MODEL_ID,
    DEFAULT_PORT,
    HEALTH_STATUSES,
    JSON_STOP_EVERY,
    SCENE_MAX_EDGE,
    SCENE_MAX_NEW_TOKENS,
    SCENE_MIN_TOWER_EDGE,
    SCENE_NATIVE_CROP,
    missing_package_name,
)
from desk_pilot.vision.capture import ensure_min_edge, fit_max_edge, tower_spatial_ok
from desk_pilot.vision.scene import SCENE_PROMPT, empty_scene, json_object_complete, parse_scene_text

IMAGE_TOKEN_INDEX = -200

_state_lock = threading.Lock()
_infer_lock = threading.Lock()
_status = "loading"
_phase = "loading_weights"
_mode = "fastvlm"
_device: str | None = None
_error: str | None = None
_note: str | None = None
_model = None
_tokenizer = None
_max_edge = SCENE_MAX_EDGE


def health_payload() -> dict[str, Any]:
    with _state_lock:
        payload: dict[str, Any] = {
            "status": _status,
            "phase": _phase,
            "mode": _mode,
            "device": _device,
            "model": DEFAULT_MODEL_ID if _mode == "fastvlm" else None,
            "max_edge": _max_edge,
            "min_tower_edge": SCENE_MIN_TOWER_EDGE,
            "max_new_tokens": SCENE_MAX_NEW_TOKENS,
        }
        if _error:
            payload["error"] = _error
        if _note:
            payload["note"] = _note
        return payload


def set_state(
    *,
    status: str | None = None,
    phase: str | None = None,
    mode: str | None = None,
    device: str | None = None,
    error: str | None = None,
    note: str | None = None,
) -> None:
    global _status, _phase, _mode, _device, _error, _note
    with _state_lock:
        if status is not None:
            _status = status if status in HEALTH_STATUSES else "error"
        if phase is not None:
            _phase = phase
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
    global _model, _tokenizer, _max_edge
    try:
        import torch
        from PIL import Image  # noqa: F401 — fail fast if pillow missing
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:  # noqa: BLE001
        set_state(
            status="error",
            phase="",
            mode="fastvlm",
            error=_short_exc(exc, prefix="FastVLM deps missing"),
            note="UI stays up; pip install -r requirements-vision.txt, then restart.",
        )
        return
    try:
        cuda = bool(torch.cuda.is_available())
        device = "cuda" if cuda else "cpu"
        dtype = torch.float16 if cuda else torch.float32
        _max_edge = SCENE_NATIVE_CROP
        _configure_torch(torch, cuda=cuda)
        set_state(
            status="loading",
            phase="loading_weights",
            mode="fastvlm",
            device=device,
            note=None if cuda else "CPU is OK; first load is slow. /scene uses the native 1024px crop.",
        )
        sidecar_log(f"loading {DEFAULT_MODEL_ID} device={device} dtype={dtype} crop={_max_edge}")
        tokenizer = AutoTokenizer.from_pretrained(DEFAULT_MODEL_ID, trust_remote_code=True)
        kwargs: dict[str, Any] = {
            "torch_dtype": dtype,
            "trust_remote_code": True,
            "low_cpu_mem_usage": True,
        }
        quant = _quant_load_kwargs(cuda)
        kwargs.update(quant)
        if "load_in_8bit" in kwargs or "load_in_4bit" in kwargs:
            kwargs.pop("torch_dtype", None)
        if cuda and "load_in_8bit" not in kwargs and "load_in_4bit" not in kwargs:
            kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(DEFAULT_MODEL_ID, **kwargs)
        if not cuda and "load_in_8bit" not in kwargs:
            model = model.to("cpu")
        model.eval()
        if hasattr(model, "config"):
            try:
                model.config.use_cache = True
            except Exception:
                pass
        _tokenizer = tokenizer
        _model = model
        quant_note = " 8-bit" if "load_in_8bit" in kwargs else (" 4-bit" if "load_in_4bit" in kwargs else "")
        set_state(
            status="ready",
            phase="",
            mode="fastvlm",
            device=device,
            error="",
            note=(
                None
                if cuda
                else f"CPU inference at native {SCENE_NATIVE_CROP}px crop / {SCENE_MAX_NEW_TOKENS} tokens (greedy)."
            ),
        )
        sidecar_log(
            f"ready device={device}{quant_note} crop={_max_edge} max_new_tokens={SCENE_MAX_NEW_TOKENS}"
        )
    except Exception as exc:  # noqa: BLE001
        set_state(
            status="error",
            phase="",
            mode="fastvlm",
            error=_short_exc(exc, prefix="FastVLM load failed"),
            note=traceback.format_exc()[-400:],
        )


def _configure_torch(torch: Any, *, cuda: bool) -> None:
    if cuda:
        return
    try:
        n = os.cpu_count() or 4
        torch.set_num_threads(max(1, min(8, n)))
        torch.set_num_interop_threads(1)
    except Exception:
        pass


def _quant_load_kwargs(cuda: bool) -> dict[str, Any]:
    """Optional bitsandbytes 8-bit on CUDA. Off unless DESK_PILOT_FASTVLM_8BIT=1 (Windows can fail)."""
    flag = (os.environ.get("DESK_PILOT_FASTVLM_8BIT") or "").strip().lower()
    if flag not in {"1", "true", "yes", "on"} or not cuda:
        return {}
    try:
        import bitsandbytes  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        sidecar_log(f"8-bit skipped (bitsandbytes: {exc})")
        return {}
    sidecar_log("loading with bitsandbytes 8-bit")
    return {"load_in_8bit": True, "device_map": "auto"}


def enter_stub(reason: str = "stub mode") -> None:
    set_state(status="ready", phase="", mode="stub", device=None, error="", note=reason)


def sidecar_log(message: str) -> None:
    sys.stderr.write(f"fastvlm-sidecar: {message}\n")
    sys.stderr.flush()


def infer_scene(path: str | None, image_bytes: bytes | None, window: str, region: dict[str, int]) -> dict[str, Any]:
    with _state_lock:
        mode = _mode
        status = _status
        model = _model
        tokenizer = _tokenizer
        max_edge = _max_edge
    if status not in {"ready", "busy"}:
        return empty_scene(window=window, region=region, note=f"sidecar not ready ({status})")
    if mode == "stub" or model is None or tokenizer is None:
        return empty_scene(window=window, region=region, note="stub: FastVLM not loaded")
    image = _open_image(path, image_bytes)
    if image is None:
        return empty_scene(window=window, region=region, note="could not open screenshot")
    src_w, src_h = int(image.size[0]), int(image.size[1])
    image = ensure_min_edge(fit_max_edge(image, max_edge), SCENE_MIN_TOWER_EDGE)
    sidecar_log(
        f"vision image {src_w}x{src_h} -> {int(image.size[0])}x{int(image.size[1])} "
        f"(native crop {SCENE_NATIVE_CROP})"
    )
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
    px = _pixel_values(model, image)
    px = px.to(model.device, dtype=getattr(model, "dtype", px.dtype))
    stopping = _json_stopping(tokenizer, int(input_ids.shape[-1]))
    gen_kwargs: dict[str, Any] = {
        "inputs": input_ids,
        "attention_mask": attention_mask,
        "images": px,
        "max_new_tokens": SCENE_MAX_NEW_TOKENS,
        "do_sample": False,
        "use_cache": True,
    }
    pad = getattr(tokenizer, "eos_token_id", None) or getattr(tokenizer, "pad_token_id", None)
    if pad is not None:
        gen_kwargs["pad_token_id"] = pad
        gen_kwargs["eos_token_id"] = pad
    if stopping is not None:
        gen_kwargs["stopping_criteria"] = stopping
    inference = getattr(torch, "inference_mode", torch.no_grad)
    with inference():
        out = model.generate(**gen_kwargs)
    new_tokens = out[0, input_ids.shape[-1] :]
    decoded = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return str(decoded or "")


def _pixel_values(model: Any, image: Any) -> Any:
    processor = model.get_vision_tower().image_processor
    min_edge = _processor_crop_edge(processor)
    px = processor(images=image, return_tensors="pt")["pixel_values"]
    px, note = ensure_tower_pixels(px, min_edge=min_edge)
    height, width = _pixel_hw(px)
    sidecar_log(
        f"vision tower input {width}x{height} min={min_edge}"
        + (f" ({note})" if note else "")
    )
    if not tower_spatial_ok(height, width, min_edge=min_edge):
        raise RuntimeError(
            f"FastVLM tower input {width}x{height} is below native {min_edge}px crop "
            f"(would pool to 0x0, e.g. 3072x12x12 from a 768px encode)"
        )
    return px


def _processor_crop_edge(processor: Any) -> int:
    crop = getattr(processor, "crop_size", None)
    values: list[int] = []
    if isinstance(crop, dict):
        for key in ("height", "width", "h", "w"):
            try:
                values.append(int(crop.get(key) or 0))
            except (TypeError, ValueError):
                pass
    elif isinstance(crop, (list, tuple)):
        for item in crop[:2]:
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                pass
    elif isinstance(crop, (int, float)):
        values.append(int(crop))
    native = max(values) if values else 0
    return max(native, SCENE_MIN_TOWER_EDGE)


def _pixel_hw(px: Any) -> tuple[int, int]:
    shape = getattr(px, "shape", None)
    if shape is None or len(shape) < 2:
        return 0, 0
    return int(shape[-2]), int(shape[-1])


def ensure_tower_pixels(px: Any, *, min_edge: int) -> tuple[Any, str | None]:
    """Upscale pixel_values so H and W are at least FastVLM's native crop (no downscale)."""
    height, width = _pixel_hw(px)
    if tower_spatial_ok(height, width, min_edge=min_edge):
        return px, None
    import torch.nn.functional as F

    note = f"upscaled {width}x{height} -> {min_edge}x{min_edge}"
    sidecar_log(f"vision tower pixels too small; {note}")
    if getattr(px, "ndim", 0) == 4:
        px = F.interpolate(px, size=(min_edge, min_edge), mode="bilinear", align_corners=False)
        return px, note
    if getattr(px, "ndim", 0) == 5:
        batch, tiles, channels, _, _ = px.shape
        flat = px.reshape(batch * tiles, channels, height, width)
        flat = F.interpolate(flat, size=(min_edge, min_edge), mode="bilinear", align_corners=False)
        px = flat.reshape(batch, tiles, channels, min_edge, min_edge)
        return px, note
    return px, note


def _json_stopping(tokenizer: Any, prompt_len: int) -> Any | None:
    try:
        from transformers import StoppingCriteria, StoppingCriteriaList
    except Exception:
        return None

    class _JsonDone(StoppingCriteria):
        def __init__(self) -> None:
            self._prompt_len = prompt_len
            self._tok = tokenizer

        def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
            gen = input_ids[0, self._prompt_len :]
            n = int(gen.shape[-1])
            if n < 8:
                return False
            # Decode is the expensive part; checking every token adds latency.
            every = max(1, int(JSON_STOP_EVERY))
            if n % every != 0 and n < SCENE_MAX_NEW_TOKENS:
                return False
            text = self._tok.decode(gen, skip_special_tokens=True)
            return json_object_complete(text)

    return StoppingCriteriaList([_JsonDone()])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sidecar_log(fmt % args)

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
        started = time.perf_counter()
        size = ""
        try:
            image = _open_image(path, image_bytes)
            if image is not None:
                size = f"{image.size[0]}x{image.size[1]}"
        except Exception:
            size = ""
        sidecar_log(
            f"/scene start window={window!r} image={size or 'none'} "
            f"native_crop={SCENE_NATIVE_CROP}"
        )
        prev = health_payload().get("status")
        got_lock = _infer_lock.acquire(blocking=False)
        scene: dict[str, Any]
        if not got_lock:
            # A previous generate is still running. Waiting here would stack another
            # 1024² decode behind `_infer_lock` and freeze the next observe.
            scene = empty_scene(window=window, region=region, note="sidecar busy; skipped stacked /scene")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            scene["elapsed_ms"] = elapsed_ms
            sidecar_log(f"/scene skip busy elapsed_ms={elapsed_ms}")
            self._send(200, scene)
            return
        if prev in {"ready", "busy"}:
            set_state(status="busy", phase="inferring")
        scene = empty_scene(window=window, region=region, note="infer failed")
        try:
            scene = infer_scene(path, image_bytes, window, region)
        finally:
            _infer_lock.release()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if health_payload().get("status") == "busy":
                set_state(status="ready", phase="")
            if isinstance(scene, dict):
                scene = dict(scene)
                scene["elapsed_ms"] = elapsed_ms
            count = len((scene if isinstance(scene, dict) else {}).get("elements") or [])
            note = str((scene or {}).get("note") or "")
            extra = f" note={note[:80]!r}" if note else ""
            sidecar_log(f"/scene end elapsed_ms={elapsed_ms} elements={count}{extra}")
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
        set_state(status="loading", phase="loading_weights", mode="fastvlm", note="Loading vision model…")
        threading.Thread(target=load_model, name="fastvlm-load", daemon=True).start()
    try:
        serve(args.host, int(args.port))
    except OSError as exc:
        sys.stderr.write(f"fastvlm-sidecar bind failed: {exc}\n")
        return 1
    return 0


def _short_exc(exc: BaseException, *, prefix: str) -> str:
    blob = f"{type(exc).__name__}: {exc}"
    pkg = missing_package_name(blob)
    if pkg:
        return f"missing {pkg}"
    if len(blob) <= 48:
        return blob
    return f"{prefix}: {type(exc).__name__}"


if __name__ == "__main__":
    raise SystemExit(main())
