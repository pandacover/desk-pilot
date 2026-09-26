import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

from desk_pilot.vision.client import SceneClient, StubSceneClient
from desk_pilot.vision.scene import compact_scene, empty_scene, normalize_scene, parse_scene_text


class SceneJsonTests(unittest.TestCase):
    def test_compact_no_pretty_print(self) -> None:
        scene = {
            "window": "Helium",
            "region": {"x": 0, "y": 80, "w": 1280, "h": 700},
            "elements": [
                {"id": "img_0", "label": "cat", "role": "image", "box": [10, 90, 80, 160], "click": [45, 125]}
            ],
        }
        text = compact_scene(scene)
        self.assertNotIn("\n", text)
        self.assertIn('"img_0"', text)
        self.assertTrue(text.startswith("{"))

    def test_parse_fenced_json_and_relative_boxes(self) -> None:
        raw = """```json
        {"window":"Pics","elements":[{"id":"img_0","label":"Cat","role":"image","box":[0.1,0.2,0.3,0.4],"click":[0.2,0.3]}]}
        ```"""
        scene = parse_scene_text(raw, window="Pics", region={"x": 100, "y": 200, "w": 1000, "h": 500})
        self.assertEqual(scene["window"], "Pics")
        el = scene["elements"][0]
        self.assertEqual(el["box"], [200, 300, 400, 400])
        self.assertEqual(el["id"], "img_0")

    def test_crop_relative_boxes_are_offset(self) -> None:
        scene = normalize_scene(
            {"elements": [{"label": "Save", "role": "button", "box": [10, 10, 90, 40]}]},
            window="App",
            region={"x": 80, "y": 120, "w": 400, "h": 300},
        )
        self.assertEqual(scene["elements"][0]["box"], [90, 130, 170, 160])
        self.assertEqual(scene["elements"][0]["click"], [130, 145])

    def test_caps_at_20_elements(self) -> None:
        raw = {
            "elements": [
                {"label": str(i), "box": [0, i, 10, i + 5]} for i in range(40)
            ]
        }
        scene = normalize_scene(raw, region={"x": 0, "y": 0, "w": 100, "h": 400})
        self.assertEqual(len(scene["elements"]), 20)

    def test_empty_scene_note(self) -> None:
        scene = empty_scene(window="X", region={"x": 1, "y": 2, "w": 3, "h": 4}, note="stub")
        self.assertEqual(scene["elements"], [])
        self.assertEqual(scene["note"], "stub")
        self.assertEqual(scene["region"]["x"], 1)

    def test_json_object_complete(self) -> None:
        from desk_pilot.vision.scene import json_object_complete

        self.assertFalse(json_object_complete(""))
        self.assertFalse(json_object_complete("{"))
        self.assertTrue(json_object_complete('{"a":1}'))
        self.assertTrue(json_object_complete('noise {"a": "{x}"} trailing'))
        self.assertFalse(json_object_complete('{"a": "{still}'))


class StubClientTests(unittest.TestCase):
    def test_stub_is_ready_without_http(self) -> None:
        client = StubSceneClient()
        self.assertTrue(client.is_ready())
        scene = client.infer_scene(window="Desktop", region={"x": 0, "y": 0, "w": 10, "h": 10})
        self.assertEqual(scene["elements"], [])
        self.assertIn("stub", scene.get("note") or "")
        self.assertEqual(len(client.calls), 1)

    def test_infer_scene_read_timeout_fail_open(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = int(sock.getsockname()[1])
        client = SceneClient(f"http://127.0.0.1:{port}", timeout=0.4)
        try:
            started = time.time()
            scene = client.infer_scene(
                window="Helium",
                region={"x": 0, "y": 0, "w": 10, "h": 10},
                timeout=0.4,
            )
            elapsed = time.time() - started
            self.assertLess(elapsed, 2.5)
            self.assertEqual(scene["elements"], [])
            self.assertIn("failed", (scene.get("note") or "").lower())
        finally:
            client.close()
            sock.close()

    def test_default_infer_timeout_is_capped(self) -> None:
        from desk_pilot.vision.client import SCENE_INFER_TIMEOUT

        self.assertGreaterEqual(SCENE_INFER_TIMEOUT, 30.0)
        self.assertLessEqual(SCENE_INFER_TIMEOUT, 45.0)
        client = SceneClient("http://127.0.0.1:9")
        try:
            self.assertEqual(client.timeout, SCENE_INFER_TIMEOUT)
        finally:
            client.close()


class SidecarHttpTests(unittest.TestCase):
    def test_stub_sidecar_health_and_scene(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.close()
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "desk_pilot.vision.sidecar",
                "--stub",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        client = SceneClient(f"http://127.0.0.1:{port}", timeout=5.0)
        try:
            health = {"status": "error"}
            for _ in range(40):
                health = client.health()
                if health.get("status") == "ready":
                    break
                time.sleep(0.1)
            self.assertEqual(health.get("status"), "ready")
            self.assertEqual(health.get("mode"), "stub")
            scene = client.infer_scene(window="Helium", region={"x": 0, "y": 80, "w": 100, "h": 50})
            self.assertEqual(scene["window"], "Helium")
            self.assertEqual(scene["region"]["y"], 80)
            self.assertIn("elements", scene)
            proc.terminate()
            try:
                out = (proc.stdout.read() if proc.stdout else b"") or b""
            except Exception:
                out = b""
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            blob = out.decode("utf-8", errors="replace")
            self.assertIn("/scene start", blob)
            self.assertIn("elapsed_ms=", blob)
            return
        finally:
            client.close()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def test_vision_modules_do_not_import_torch(self) -> None:
        before_torch = "torch" in sys.modules
        import desk_pilot.vision.capture as capture
        import desk_pilot.vision.client as client
        import desk_pilot.vision.manager as manager
        import desk_pilot.vision.scene as scene
        import desk_pilot.vision.sidecar as sidecar

        if not before_torch:
            self.assertNotIn("torch", sys.modules)
        self.assertTrue(hasattr(capture, "capture_content_screenshot"))
        self.assertTrue(hasattr(client, "SceneClient"))
        self.assertTrue(hasattr(manager, "SidecarManager"))
        self.assertTrue(callable(scene.compact_scene))
        self.assertTrue(callable(sidecar.main))


class CaptureTests(unittest.TestCase):
    def test_mock_capture_compresses(self) -> None:
        from desk_pilot.desktop.mock import MockDesktop
        from desk_pilot.vision.capture import capture_content_screenshot

        desk = MockDesktop()
        result = capture_content_screenshot(desk)
        self.assertTrue(result["ok"])
        self.assertTrue(Path(result["path"]).is_file())
        self.assertIn("region", result)
        self.assertTrue(result["path"].endswith(".scene.jpg") or result["path"].endswith(".png"))

    def test_fit_max_edge_shrinks_long_side(self) -> None:
        from PIL import Image

        from desk_pilot.vision.capture import fit_max_edge

        image = Image.new("RGB", (2000, 1000), "red")
        out = fit_max_edge(image, 512)
        self.assertLessEqual(max(out.size), 512)
        self.assertEqual(out.size[0] / out.size[1], 2)

    def test_ensure_min_edge_upscales_small_shots(self) -> None:
        from PIL import Image

        from desk_pilot.vision.capture import ensure_min_edge

        image = Image.new("RGB", (400, 200), "red")
        out = ensure_min_edge(image, 1024)
        self.assertGreaterEqual(max(out.size), 1024)

    def test_tower_spatial_ok_rejects_768_and_12x12(self) -> None:
        from desk_pilot.vision.capture import tower_spatial_ok

        # Live CUDA error: 768px encode → FastViT 12×12 features → pool to 0×0.
        self.assertFalse(tower_spatial_ok(12, 12, min_edge=1024))
        self.assertFalse(tower_spatial_ok(768, 768, min_edge=1024))
        self.assertTrue(tower_spatial_ok(1024, 1024, min_edge=1024))
        self.assertTrue(tower_spatial_ok(1024, 1024))


class ConfigFastvlmTests(unittest.TestCase):
    def test_env_disables_fastvlm(self) -> None:
        import os

        from desk_pilot.app.config import Settings

        settings = Settings(fastvlm_enabled=True)
        old = os.environ.get("DESK_PILOT_FASTVLM")
        os.environ["DESK_PILOT_FASTVLM"] = "0"
        try:
            self.assertFalse(settings.effective_fastvlm())
        finally:
            if old is None:
                os.environ.pop("DESK_PILOT_FASTVLM", None)
            else:
                os.environ["DESK_PILOT_FASTVLM"] = old


class HealthErrorChipTests(unittest.TestCase):
    def test_timm_importerror_is_short(self) -> None:
        from desk_pilot.vision import missing_package_name, short_health_error

        live = (
            "ImportError: This modeling file requires the timm library but it was not found "
            "in your environment. You can install it with pip: `pip install timm`. "
            "Please note that you may need to restart your runtime after installation."
        )
        self.assertEqual(missing_package_name(live), "timm")
        self.assertEqual(short_health_error(live), "missing timm")

    def test_status_label_shows_missing_package(self) -> None:
        from desk_pilot.vision.manager import SidecarManager

        manager = SidecarManager(enabled=True, stub=True)
        manager._last_health = {
            "status": "error",
            "error": (
                "FastVLM load failed: ImportError: This modeling file requires the timm "
                "library but it was not found in your environment. Run `pip install timm`"
            ),
        }
        self.assertEqual(manager.status_label(), "Vision: missing timm")

    def test_busy_health_chip_says_inferring(self) -> None:
        from desk_pilot.vision.manager import SidecarManager

        manager = SidecarManager(enabled=True, stub=True)
        manager._last_health = {"status": "busy", "phase": "inferring", "mode": "fastvlm"}
        self.assertEqual(manager.status_label(), "Vision inferring…")
        self.assertTrue(manager.is_ready())

    def test_scene_speed_constants(self) -> None:
        from desk_pilot.vision import SCENE_MAX_EDGE, SCENE_MIN_TOWER_EDGE, SCENE_NATIVE_CROP, SCENE_MAX_NEW_TOKENS

        self.assertEqual(SCENE_NATIVE_CROP, 1024)
        self.assertGreaterEqual(SCENE_MAX_EDGE, 1024)
        self.assertGreaterEqual(SCENE_MIN_TOWER_EDGE, 1024)
        self.assertGreaterEqual(SCENE_MAX_NEW_TOKENS, 128)
        self.assertLessEqual(SCENE_MAX_NEW_TOKENS, 256)

    def test_processor_crop_never_below_native(self) -> None:
        from types import SimpleNamespace

        from desk_pilot.vision.sidecar import _processor_crop_edge

        self.assertEqual(
            _processor_crop_edge(SimpleNamespace(crop_size={"height": 1024, "width": 1024})),
            1024,
        )
        self.assertEqual(_processor_crop_edge(SimpleNamespace(crop_size={"height": 768, "width": 768})), 1024)
        self.assertEqual(_processor_crop_edge(SimpleNamespace()), 1024)

    def test_health_busy_payload(self) -> None:
        from desk_pilot.vision.sidecar import enter_stub, health_payload, set_state

        enter_stub("test")
        try:
            set_state(status="busy", phase="inferring")
            payload = health_payload()
            self.assertEqual(payload["status"], "busy")
            self.assertEqual(payload["phase"], "inferring")
        finally:
            enter_stub("test")

    def test_short_health_error_passthrough(self) -> None:
        from desk_pilot.vision import short_health_error

        self.assertEqual(short_health_error("missing timm"), "missing timm")
        self.assertEqual(short_health_error("sidecar unreachable"), "sidecar unreachable")

    def test_requirements_vision_lists_timm(self) -> None:
        text = Path(__file__).resolve().parents[1].joinpath("requirements-vision.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("timm", text)
        self.assertIn("einops", text)
        self.assertIn("sentencepiece", text)


if __name__ == "__main__":
    unittest.main()
