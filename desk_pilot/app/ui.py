from __future__ import annotations

import argparse
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from desk_pilot import APP_NAME, DEFAULT_MAX_STEPS, DEFAULT_MODEL, __version__
from desk_pilot.agent.loop import AgentLoop, RunResult
from desk_pilot.app.config import Settings, load_settings, save_settings
from desk_pilot.desktop import get_backend, is_windows
from desk_pilot.llm.openrouter import OpenRouterClient


LOG_COLORS = {
    "info": "#d0d4dc",
    "observe": "#7fdbda",
    "plan": "#c4b5fd",
    "act": "#fbbf24",
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
        self._log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._running = False

        self._build()
        self._bind_keys()
        self.after(120, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_status()

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
            text="Local agent: UI Automation → OpenRouter → click/type → verify. Paste your key, then run a goal.",
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
        self.stop_btn.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            goal_frame,
            text="The agent will control this PC until you click STOP or it calls done/fail (max steps in Settings).",
            text_color="#9aa3b2",
            wraplength=480,
            anchor="w",
            justify="left",
        ).grid(row=3, column=0, sticky="w", padx=14, pady=(0, 12))

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

        ctk.CTkButton(settings, text="Save settings", command=self._save_settings).grid(
            row=9, column=0, sticky="ew", padx=14, pady=(12, 14)
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
        self.log.insert("end", "Ready. Paste an OpenRouter key, then Run.\n")
        self.log.configure(state="disabled")
        inner = getattr(self.log, "_textbox", None)
        if inner is not None:
            for kind, color in LOG_COLORS.items():
                inner.tag_configure(kind, foreground=color)

    def _bind_keys(self) -> None:
        self.bind("<Control-Return>", lambda _e: self._on_run())
        self.bind("<Escape>", lambda _e: self._on_stop())

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def _refresh_status(self) -> None:
        dry = self.backend.dry_run
        key = self._effective_key()
        mode = "DRY-RUN" if dry else "WINDOWS"
        key_state = "key set" if key else "no API key"
        self.status_chip.configure(text=f"  {mode}  ·  {key_state}  ")
        if self.settings.key_from_env():
            self.env_hint.configure(
                text="Using OPENROUTER_API_KEY from the environment (overrides the saved key)."
            )
        else:
            self.env_hint.configure(
                text="Saved on this PC only. Env OPENROUTER_API_KEY overrides this field."
            )

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
        )

    def _save_settings(self) -> None:
        self.settings = self._read_form()
        path = save_settings(self.settings)
        self._log("info", f"Saved settings to {path}")
        self._refresh_status()

    def _effective_key(self) -> str:
        self.settings.openrouter_api_key = self.key_var.get().strip()
        return self.settings.effective_api_key()

    def _on_run(self) -> None:
        if self._running:
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
            if self.backend.dry_run:
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
        self._stop.clear()
        self._running = True
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.goal_box.configure(state="disabled")
        self._log("info", "Starting agent loop…")
        self._worker = threading.Thread(target=self._worker_run, args=(goal, key), daemon=True)
        self._worker.start()

    def _worker_run(self, goal: str, key: str) -> None:
        from desk_pilot.desktop.com import com_thread

        client = OpenRouterClient(
            key,
            self.settings.model or DEFAULT_MODEL,
            reasoning_effort=self.settings.reasoning_effort or "low",
        )
        try:
            with com_thread():
                agent = AgentLoop(
                    backend=self.backend,
                    llm=client,
                    max_steps=self.settings.max_steps,
                    on_log=lambda kind, msg: self._log_queue.put((kind, msg)),
                    stop_event=self._stop,
                )
                result = agent.run(goal)
        except Exception as exc:  # noqa: BLE001
            result = RunResult("fail", f"Agent crashed: {exc}", 0, self.backend.dry_run)
            self._log_queue.put(("error", result.message))
        finally:
            client.close()
        self._log_queue.put(("_finished", result.status + "\n" + result.message))

    def _on_stop(self) -> None:
        if not self._running:
            return
        self._stop.set()
        self._log("stop", "Stop requested — waiting for the current model/tool call to finish.")

    def _finish_ui(self, payload: str) -> None:
        self._running = False
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
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

    def _drain_logs(self) -> None:
        try:
            while True:
                kind, message = self._log_queue.get_nowait()
                if kind == "_finished":
                    self._finish_ui(message)
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
        self.destroy()


def run_app(*, force_mock: bool = False) -> None:
    app = DeskPilotApp(force_mock=force_mock)
    app.mainloop()
