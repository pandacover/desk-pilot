from __future__ import annotations

import argparse
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from typing import Any

import customtkinter as ctk

from desk_pilot import APP_NAME, DEFAULT_MAX_STEPS, DEFAULT_MODEL
from desk_pilot.agent.loop import AgentLoop, RunResult
from desk_pilot.agent.guide import is_guide_goal
from desk_pilot.app.config import Settings, load_settings, save_settings
from desk_pilot.app.run_control import STOP_ABANDON_SECONDS, RunGate
from desk_pilot.desktop import get_backend, is_windows
from desk_pilot.llm.openrouter import OpenRouterClient


LOG_COLORS = {
    "info": "#d0d4dc",
    "observe": "#7fdbda",
    "plan": "#c4b5fd",
    "act": "#fbbf24",
    "guide": "#fde68a",
    "sketch": "#fbbf24",
    "done": "#4ade80",
    "fail": "#f87171",
    "error": "#f87171",
    "stop": "#fb923c",
}


class DeskPilotApp(ctk.CTk):
    def __init__(self, *, force_mock: bool = False) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  ·  computer-use agent")
        self.geometry("980x720")
        self.minsize(820, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.settings = load_settings()
        self.force_mock = force_mock
        self.backend = get_backend(force_mock=force_mock or not is_windows())
        self._log_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._continue = threading.Event()
        self._worker: threading.Thread | None = None
        self._gate = RunGate()
        self._abandon_job: Any = None
        self._guide_waiting = False
        self._testing_sketch = False
        self._overlay_jobs: queue.Queue[Any] = queue.Queue()
        self._vision = None

        self._build()
        self._bind_keys()
        self._install_overlay_pump()
        self.after(120, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_status()
        # Sidecar starts after the window is shown so FastVLM load never blocks first paint.
        self.after(80, self._boot_vision)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(16, 4))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=APP_NAME,
            font=ctk.CTkFont(size=26, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.status_chip = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8,
        )
        self.status_chip.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            header,
            text="Local agent: UIA + FastVLM scene → OpenRouter → click/type. Vision loads in the background after this window opens.",
            text_color="#9aa3b2",
            anchor="w",
            wraplength=720,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        goal_frame = ctk.CTkFrame(self)
        goal_frame.grid(row=1, column=0, sticky="nsew", padx=(18, 8), pady=8)
        goal_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(goal_frame, text="Goal", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self.goal_box = ctk.CTkTextbox(goal_frame, height=120, font=ctk.CTkFont(size=14))
        self.goal_box.grid(row=1, column=0, sticky="ew", padx=14)
        self.goal_box.insert("1.0", "Open Notepad and type hello")

        buttons = ctk.CTkFrame(goal_frame, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=14, pady=12)
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=2)
        buttons.grid_columnconfigure(2, weight=1)
        self.run_btn = ctk.CTkButton(
            buttons,
            text="Run",
            height=48,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#15803d",
            hover_color="#166534",
            command=self._on_run,
        )
        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = ctk.CTkButton(
            buttons,
            text="STOP",
            height=48,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#b91c1c",
            hover_color="#7f1d1d",
            command=self._on_stop,
            state="disabled",
        )
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.continue_btn = ctk.CTkButton(
            buttons,
            text="Continue",
            height=48,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#1d4ed8",
            hover_color="#1e3a8a",
            command=self._on_continue,
            state="disabled",
        )
        self.continue_btn.grid(row=0, column=2, sticky="ew")
        self.guide_hint = ctk.CTkLabel(
            goal_frame,
            text="",
            text_color="#fde68a",
            wraplength=480,
            anchor="w",
            justify="left",
        )
        self.guide_hint.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 4))
        ctk.CTkLabel(
            goal_frame,
            text="Auto: the agent clicks for you. How-to goals (or Guide mode) sketch a step and wait until you click Continue.",
            text_color="#9aa3b2",
            wraplength=480,
            anchor="w",
            justify="left",
        ).grid(row=4, column=0, sticky="w", padx=14, pady=(0, 12))

        settings = ctk.CTkFrame(self)
        settings.grid(row=1, column=1, sticky="nsew", padx=(8, 18), pady=8)
        settings.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(settings, text="Settings", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )

        ctk.CTkLabel(settings, text="OpenRouter API key", anchor="w", text_color="#9aa3b2").grid(
            row=1, column=0, sticky="w", padx=14
        )
        self.key_var = tk.StringVar(value=self.settings.openrouter_api_key)
        self.key_entry = ctk.CTkEntry(settings, textvariable=self.key_var, show="•", placeholder_text="sk-or-v1-…")
        self.key_entry.grid(row=2, column=0, sticky="ew", padx=14)
        self.show_key = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            settings,
            text="Show key",
            variable=self.show_key,
            command=self._toggle_key,
        ).grid(row=3, column=0, sticky="w", padx=14, pady=(6, 0))

        self.env_hint = ctk.CTkLabel(
            settings,
            text="",
            text_color="#7fdbda",
            anchor="w",
            justify="left",
            wraplength=300,
        )
        self.env_hint.grid(row=4, column=0, sticky="w", padx=14, pady=(4, 0))

        ctk.CTkLabel(settings, text="Model (OpenRouter)", anchor="w", text_color="#9aa3b2").grid(
            row=5, column=0, sticky="w", padx=14, pady=(8, 0)
        )
        self.model_var = tk.StringVar(value=self.settings.model or DEFAULT_MODEL)
        ctk.CTkEntry(settings, textvariable=self.model_var, placeholder_text=DEFAULT_MODEL).grid(
            row=6, column=0, sticky="ew", padx=14
        )

        row = ctk.CTkFrame(settings, fg_color="transparent")
        row.grid(row=7, column=0, sticky="ew", padx=14, pady=(8, 0))
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text="Max steps", text_color="#9aa3b2").grid(row=0, column=0, sticky="w")
        self.steps_var = tk.StringVar(value=str(self.settings.max_steps or DEFAULT_MAX_STEPS))
        ctk.CTkEntry(row, textvariable=self.steps_var, width=64).grid(row=0, column=1, sticky="e")

        self.confirm_var = tk.BooleanVar(value=self.settings.confirm_before_run)
        ctk.CTkCheckBox(
            settings,
            text="Confirm before each run",
            variable=self.confirm_var,
        ).grid(row=8, column=0, sticky="w", padx=14, pady=(8, 0))

        self.guide_var = tk.BooleanVar(value=self.settings.guide_mode)
        ctk.CTkCheckBox(
            settings,
            text="Guide mode (you click; I sketch)",
            variable=self.guide_var,
        ).grid(row=9, column=0, sticky="w", padx=14, pady=(6, 0))

        self.fastvlm_var = tk.BooleanVar(value=self.settings.fastvlm_enabled)
        ctk.CTkCheckBox(
            settings,
            text="FastVLM scene observe (local sidecar)",
            variable=self.fastvlm_var,
            command=self._on_fastvlm_toggle,
        ).grid(row=10, column=0, sticky="w", padx=14, pady=(6, 0))
        ctk.CTkLabel(
            settings,
            text="Default on. Loads apple/FastVLM-0.5B in a sidecar after this window appears. Uncheck to run UIA-only (Run enables immediately). A stuck prior run still needs STOP — FastVLM off applies to the next Run.",
            text_color="#9aa3b2",
            wraplength=300,
            anchor="w",
            justify="left",
        ).grid(row=11, column=0, sticky="w", padx=14, pady=(2, 0))

        self.test_sketch_btn = ctk.CTkButton(
            settings,
            text="Test sketch",
            command=self._on_test_sketch,
        )
        self.test_sketch_btn.grid(row=12, column=0, sticky="ew", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            settings,
            text="Draws a fixed rectangle on the desktop for 2 seconds. Use this to tell Win32 overlay apart from agent targeting.",
            text_color="#9aa3b2",
            wraplength=300,
            anchor="w",
            justify="left",
        ).grid(row=13, column=0, sticky="w", padx=14, pady=(4, 0))

        ctk.CTkButton(settings, text="Save settings", command=self._save_settings).grid(
            row=14, column=0, sticky="ew", padx=14, pady=(12, 14)
        )

        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=18, pady=(4, 16))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(log_frame, text="Live step log", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self.log = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=13), wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.log.insert("end", "Ready. Vision model loads in the background. Paste an OpenRouter key, then Run.\n")
        self.log.configure(state="disabled")
        inner = getattr(self.log, "_textbox", None)
        if inner is not None:
            for kind, color in LOG_COLORS.items():
                inner.tag_configure(kind, foreground=color)

    def _bind_keys(self) -> None:
        self.bind("<Control-Return>", lambda _e: self._on_run())
        self.bind("<Escape>", lambda _e: self._on_stop())
        self.bind("<F8>", lambda _e: self._on_continue())

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def _refresh_status(self) -> None:
        dry = self.backend.dry_run
        key = self._effective_key()
        mode = "DRY-RUN" if dry else "WINDOWS"
        key_state = "key set" if key else "no API key"
        vision = "Vision off"
        if self._vision is not None:
            vision = self._vision.status_label()
        elif self.settings.effective_fastvlm():
            vision = "Loading vision model…"
        self.status_chip.configure(text=f"  {mode}  ·  {key_state}  ·  {vision}  ")
        if self.settings.key_from_env():
            self.env_hint.configure(
                text="Using OPENROUTER_API_KEY from the environment (overrides the saved key)."
            )
        else:
            self.env_hint.configure(
                text="Saved on this PC only. Env OPENROUTER_API_KEY overrides this field."
            )
        self._sync_run_button()

    def _read_form(self) -> Settings:
        try:
            steps = int(self.steps_var.get().strip() or DEFAULT_MAX_STEPS)
        except ValueError:
            steps = DEFAULT_MAX_STEPS
        steps = max(1, min(steps, 80))
        return Settings(
            openrouter_api_key=self.key_var.get().strip(),
            model=(self.model_var.get().strip() or DEFAULT_MODEL),
            max_steps=steps,
            reasoning_effort=self.settings.reasoning_effort or "low",
            confirm_before_run=bool(self.confirm_var.get()),
            guide_mode=bool(self.guide_var.get()),
            fastvlm_enabled=bool(self.fastvlm_var.get()),
        )

    def _save_settings(self) -> None:
        self.settings = self._read_form()
        path = save_settings(self.settings)
        self._log("info", f"Saved settings to {path}")
        self._refresh_status()
        self._boot_vision()

    def _effective_key(self) -> str:
        self.settings.openrouter_api_key = self.key_var.get().strip()
        return self.settings.effective_api_key()

    def _boot_vision(self) -> None:
        from desk_pilot.vision.manager import SidecarManager

        enabled = self.settings.effective_fastvlm()
        stub = bool(self.backend.dry_run)
        current = self._vision
        if current is not None and current.enabled == enabled and current.stub == stub:
            current.poll()
            self._refresh_status()
            return
        if current is not None:
            current.stop()
            self._vision = None
        manager = SidecarManager(
            enabled=enabled,
            stub=stub,
            on_log=lambda kind, msg: self._log_queue.put((kind, msg)),
        )
        self._vision = manager
        manager.start()
        self._poll_vision()

    def _on_fastvlm_toggle(self) -> None:
        self.settings = self._read_form()
        self._boot_vision()
        if self._gate.running:
            self._log(
                "info",
                "FastVLM change applies to the next Run. If this run is stuck, click STOP "
                "(Run unlocks after a few seconds even if observe is blocked).",
            )

    def _poll_vision(self) -> None:
        vision = self._vision
        if vision is None:
            return
        vision.poll()
        self._refresh_status()
        if not vision.enabled or vision.is_ready():
            return
        died = vision._owned and vision.process is not None and vision.process.poll() is not None
        if died:
            return
        self.after(400, self._poll_vision)

    def _vision_blocks_run(self) -> bool:
        if not self.settings.effective_fastvlm():
            return False
        vision = self._vision
        if vision is None:
            return True
        return not vision.is_ready()

    def _sync_run_button(self) -> None:
        if self._gate.running:
            self.run_btn.configure(state="disabled")
            return
        if self._vision_blocks_run():
            self.run_btn.configure(state="disabled")
        else:
            self.run_btn.configure(state="normal")

    def _on_run(self) -> None:
        if self._gate.running:
            return
        if self._vision_blocks_run():
            self._log("info", "Waiting for the vision model… Run is disabled until FastVLM is ready.")
            return
        self.settings = self._read_form()
        key = self.settings.effective_api_key()
        if not key:
            messagebox.showerror(
                APP_NAME,
                "Paste an OpenRouter API key in Settings first (or set OPENROUTER_API_KEY).",
            )
            self._log("error", "Refused to run: no API key.")
            return
        goal = self.goal_box.get("1.0", "end").strip()
        if not goal:
            messagebox.showerror(APP_NAME, "Enter a goal, for example: Open Notepad and type hello")
            return
        if self.settings.confirm_before_run:
            guiding = self.settings.guide_mode or is_guide_goal(goal)
            if guiding:
                prompt = (
                    "Guide mode: Desk Pilot will sketch each step. You click and type. "
                    "It will not move the mouse or keyboard for those steps.\n\n"
                    f"Goal:\n{goal}\n\nContinue?"
                )
            elif self.backend.dry_run:
                prompt = (
                    "Dry-run mode: Desk Pilot will not move the real mouse or keyboard.\n\n"
                    f"Goal:\n{goal}\n\nContinue?"
                )
            else:
                prompt = (
                    "Desk Pilot will move the mouse and type on this Windows PC until you click STOP "
                    "or the agent finishes.\n\n"
                    f"Goal:\n{goal}\n\nContinue?"
                )
            if not messagebox.askokcancel(f"{APP_NAME} — confirm", prompt):
                self._log("info", "Run cancelled.")
                return
        save_settings(self.settings)
        self._cancel_abandon_job()
        self._stop = threading.Event()
        self._continue = threading.Event()
        token = self._gate.begin()
        self._guide_waiting = False
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.continue_btn.configure(state="disabled")
        self.goal_box.configure(state="disabled")
        self._log("info", "Starting agent loop…")
        self._worker = threading.Thread(target=self._worker_run, args=(goal, key, token), daemon=True)
        self._worker.start()

    def _worker_run(self, goal: str, key: str, token: int) -> None:
        from desk_pilot.desktop.com import com_thread

        client = OpenRouterClient(
            key,
            self.settings.model or DEFAULT_MODEL,
            reasoning_effort=self.settings.reasoning_effort or "low",
        )
        try:
            with com_thread():
                vision = self._vision
                agent = AgentLoop(
                    backend=self.backend,
                    llm=client,
                    max_steps=self.settings.max_steps,
                    on_log=lambda kind, msg: self._log_queue.put((kind, msg)),
                    stop_event=self._stop,
                    force_guide=self.settings.guide_mode,
                    continue_event=self._continue,
                    on_guide_step=lambda text: self._log_queue.put(("_guide", text)),
                    scene_client=vision.scene_client() if vision else None,
                )
                result = agent.run(goal)
        except Exception as exc:  # noqa: BLE001
            result = RunResult("fail", f"Agent crashed: {exc}", 0, self.backend.dry_run)
            self._log_queue.put(("error", result.message))
        finally:
            client.close()
        self._log_queue.put(("_finished", f"{token}\n{result.status}\n{result.message}"))

    def _on_test_sketch(self) -> None:
        if getattr(self, "_testing_sketch", False):
            return
        self._testing_sketch = True
        self.test_sketch_btn.configure(state="disabled")
        self._log("info", "Test sketch: drawing a fixed rectangle for 2 seconds…")
        threading.Thread(target=self._test_sketch_worker, daemon=True).start()

    def _test_sketch_worker(self) -> None:
        import time

        from desk_pilot.desktop.rects import SKIP_NO_RECT, TEST_SKETCH_RECT, TEST_SKETCH_SECONDS, as_rect

        box = as_rect(TEST_SKETCH_RECT)
        try:
            result = self.backend.show_highlight(box)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "skipped": False, "error": f"{type(exc).__name__}: {exc}", "rect": box}
        if not isinstance(result, dict):
            result = {
                "ok": bool(result),
                "skipped": False,
                "error": None if result else "show_highlight returned no result",
                "rect": box,
            }
        if result.get("ok"):
            extra = " dry-run (no Win32 overlay)" if result.get("dry_run") else ""
            if result.get("recreated"):
                extra += " hwnd recreated"
            self._log_queue.put(("sketch", f"{result.get('rect') or box} test{extra}"))
        elif result.get("skipped"):
            self._log_queue.put(("sketch", f"skipped: {result.get('error') or SKIP_NO_RECT}"))
        else:
            self._log_queue.put(("sketch", f"failed: {result.get('error') or 'overlay error'}"))
        time.sleep(TEST_SKETCH_SECONDS)
        try:
            self.backend.hide_highlight()
        except Exception as exc:  # noqa: BLE001
            self._log_queue.put(("sketch", f"failed: hide {exc}"))
        self._log_queue.put(("info", "Test sketch finished."))
        self._log_queue.put(("_test_sketch_done", ""))

    def _on_continue(self) -> None:
        if not self._gate.running or not self._guide_waiting:
            return
        self._continue.set()
        self._log("guide", "Continue — next step.")

    def _on_stop(self) -> None:
        if not self._gate.running:
            return
        first = not self._stop.is_set()
        self._stop.set()
        self._continue.set()
        if not first:
            return
        self._log(
            "stop",
            f"Stop requested — waiting up to {STOP_ABANDON_SECONDS:g}s, then Run unlocks even if observe is stuck.",
        )
        try:
            self._abandon_job = self.after(
                int(STOP_ABANDON_SECONDS * 1000),
                self._abandon_stuck_run,
            )
        except Exception:
            self._abandon_stuck_run()

    def _abandon_stuck_run(self) -> None:
        self._abandon_job = None
        worker = self._worker
        alive = worker is not None and worker.is_alive()
        if not self._gate.abandon_stuck(alive):
            return
        self._finish_ui(
            "stopped\nStuck step abandoned — FastVLM observe was still blocked. Run is enabled again."
        )

    def _cancel_abandon_job(self) -> None:
        job = self._abandon_job
        self._abandon_job = None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except Exception:
            pass

    def _set_guide_ui(self, instruction: str) -> None:
        text = (instruction or "").strip()
        self._guide_waiting = bool(text)
        if text:
            self.guide_hint.configure(text=f"Your turn: {text}  (Continue or F8 when done)")
            self.continue_btn.configure(state="normal")
        else:
            self.guide_hint.configure(text="")
            self.continue_btn.configure(state="disabled")

    def _finish_ui(self, payload: str) -> None:
        self._cancel_abandon_job()
        self._gate.running = False
        self._guide_waiting = False
        self.stop_btn.configure(state="disabled")
        self.continue_btn.configure(state="disabled")
        self.guide_hint.configure(text="")
        self.goal_box.configure(state="normal")
        status, _, message = payload.partition("\n")
        if status == "done":
            self._log("done", message)
        elif status == "stopped":
            self._log("stop", message)
        else:
            self._log("fail", message)
        self._refresh_status()

    def _log(self, kind: str, message: str) -> None:
        self._log_queue.put((kind, message))

    def _install_overlay_pump(self) -> None:
        """HWND owner is this Tk thread. Guide/Test sketch workers post paint jobs here."""
        from desk_pilot.desktop.overlay import set_overlay_pump

        set_overlay_pump(self._overlay_pump)

    def _overlay_pump(self, fn: Any) -> None:
        self._overlay_jobs.put(fn)
        try:
            self.after(0, self._flush_overlay_jobs)
        except Exception:
            pass

    def _flush_overlay_jobs(self) -> None:
        while True:
            try:
                job = self._overlay_jobs.get_nowait()
            except queue.Empty:
                return
            try:
                job()
            except Exception:
                continue

    def _drain_logs(self) -> None:
        self._flush_overlay_jobs()
        try:
            while True:
                kind, message = self._log_queue.get_nowait()
                if kind == "_finished":
                    token_s, sep, rest = message.partition("\n")
                    if sep:
                        try:
                            token = int(token_s)
                        except ValueError:
                            token = self._gate.token
                            rest = message
                    else:
                        token = self._gate.token
                        rest = message
                    if not self._gate.finish(token):
                        continue
                    self._finish_ui(rest)
                    continue
                if kind == "_test_sketch_done":
                    self._testing_sketch = False
                    self.test_sketch_btn.configure(state="normal")
                    continue
                if kind == "_guide":
                    self._set_guide_ui(message)
                    if message:
                        self._append_log("guide", message)
                    continue
                self._append_log(kind, message)
        except queue.Empty:
            pass
        self.after(120, self._drain_logs)

    def _append_log(self, kind: str, message: str) -> None:
        self.log.configure(state="normal")
        prefix = f"{kind.upper():<8} "
        inner = getattr(self.log, "_textbox", None)
        if inner is not None:
            inner.insert("end", prefix, kind)
            inner.insert("end", message + "\n", kind)
            inner.see("end")
        else:
            self.log.insert("end", prefix + message + "\n")
            self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        self._stop.set()
        try:
            if self._vision is not None:
                self._vision.stop()
        except Exception:
            pass
        try:
            from desk_pilot.desktop.overlay import close_overlay, set_overlay_pump

            close_overlay()
            set_overlay_pump(None)
        except Exception:
            pass
        self.destroy()


def run_app(*, force_mock: bool = False) -> None:
    app = DeskPilotApp(force_mock=force_mock)
    app.mainloop()
