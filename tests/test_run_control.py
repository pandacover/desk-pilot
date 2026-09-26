import threading
import unittest

from desk_pilot.app.run_control import STOP_ABANDON_SECONDS, RunGate
from desk_pilot.vision.manager import SidecarManager


class RunGateTests(unittest.TestCase):
    def test_stale_finish_after_abandon_is_ignored(self) -> None:
        gate = RunGate()
        token = gate.begin()
        self.assertTrue(gate.running)
        self.assertTrue(gate.abandon_stuck(worker_alive=True))
        self.assertFalse(gate.running)
        self.assertFalse(gate.finish(token))
        self.assertFalse(gate.accept_finish(token))

    def test_abandon_is_noop_when_worker_already_done(self) -> None:
        gate = RunGate()
        token = gate.begin()
        self.assertFalse(gate.abandon_stuck(worker_alive=False))
        self.assertTrue(gate.running)
        self.assertTrue(gate.finish(token))
        self.assertFalse(gate.running)

    def test_new_run_after_abandon_accepts_new_token_only(self) -> None:
        gate = RunGate()
        old = gate.begin()
        gate.abandon_stuck(worker_alive=True)
        new = gate.begin()
        self.assertNotEqual(old, new)
        self.assertFalse(gate.finish(old))
        self.assertTrue(gate.finish(new))
        self.assertFalse(gate.running)

    def test_abandon_grace_is_short(self) -> None:
        self.assertGreaterEqual(STOP_ABANDON_SECONDS, 2.0)
        self.assertLessEqual(STOP_ABANDON_SECONDS, 3.0)


class FastvlmToggleClientTests(unittest.TestCase):
    def test_disabled_manager_scene_client_is_none(self) -> None:
        manager = SidecarManager(enabled=False, stub=True)
        manager.start()
        try:
            self.assertIsNone(manager.scene_client())
        finally:
            manager.stop()

    def test_toggle_off_clears_client_for_next_run(self) -> None:
        manager = SidecarManager(enabled=True, stub=True)
        manager.client = object()  # type: ignore[assignment]
        self.assertIsNotNone(manager.scene_client())
        manager.enabled = False
        self.assertIsNone(manager.scene_client())


class StopAbandonThreadTests(unittest.TestCase):
    def test_blocked_worker_does_not_keep_gate_running(self) -> None:
        gate = RunGate()
        token = gate.begin()
        blocked = threading.Event()

        def stuck() -> None:
            blocked.wait(timeout=8)

        worker = threading.Thread(target=stuck, daemon=True)
        worker.start()
        self.assertTrue(gate.abandon_stuck(worker_alive=worker.is_alive()))
        self.assertFalse(gate.running)
        self.assertFalse(gate.finish(token))
        blocked.set()
        worker.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
