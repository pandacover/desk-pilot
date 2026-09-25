from __future__ import annotations

import base64
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from desk_pilot.agent.prompts import SYSTEM_PROMPT, observation_message, user_goal_message
from desk_pilot.agent.tools import (
    TOOL_DEFINITIONS,
    TerminalCall,
    compact_json,
    dispatch_tool,
    parse_arguments,
)
from desk_pilot.desktop.base import DesktopBackend
from desk_pilot.llm.openrouter import LLMClient, LLMError


LogFn = Callable[[str, str], None]


@dataclass
class RunResult:
    status: str
    message: str
    steps: int
    dry_run: bool


@dataclass
class AgentLoop:
    backend: DesktopBackend
    llm: LLMClient
    max_steps: int = 30
    on_log: LogFn | None = None
    stop_event: threading.Event | None = None
    _messages: list[dict[str, Any]] = field(default_factory=list)

    def run(self, goal: str) -> RunResult:
        goal = (goal or "").strip()
        if not goal:
            return RunResult("fail", "Enter a goal first.", 0, self.backend.dry_run)

        self._log("info", f"Goal: {goal}")
        if self.backend.dry_run:
            self._log("info", "Dry-run desktop: actions are simulated, not sent to the OS.")

        snapshot = self.backend.list_ui()
        self._messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_goal_message(goal, snapshot, self.backend.dry_run)},
        ]
        self._log("observe", f"Window: {self._window_label(snapshot)}")
        if snapshot.get("com_error"):
            self._log("error", snapshot.get("error") or "list_ui COM failure")

        for step in range(1, self.max_steps + 1):
            if self._stopped():
                return RunResult("stopped", "Stopped by user.", step - 1, self.backend.dry_run)
            self._log("plan", f"Step {step}/{self.max_steps} — asking the model…")
            try:
                response = self.llm.complete(self._messages, TOOL_DEFINITIONS)
            except LLMError as exc:
                self._log("error", str(exc))
                return RunResult("fail", str(exc), step, self.backend.dry_run)
            except Exception as exc:  # noqa: BLE001 — surface unexpected LLM failures
                self._log("error", f"LLM error: {exc}")
                return RunResult("fail", f"LLM error: {exc}", step, self.backend.dry_run)

            tool_calls = response.get("tool_calls") or []
            content = (response.get("content") or "").strip()
            if content:
                self._log("plan", content[:400])

            if not tool_calls:
                nudge = (
                    "You must call a tool: launch_app, an action, screenshot_region, "
                    "wait_for_window, done, or fail."
                )
                self._messages.append({"role": "assistant", "content": content or ""})
                self._messages.append({"role": "user", "content": nudge})
                continue

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            }
            self._messages.append(assistant_msg)

            terminal: TerminalCall | None = None
            screenshot_path: str | None = None
            for call in tool_calls:
                if self._stopped():
                    return RunResult("stopped", "Stopped by user.", step, self.backend.dry_run)
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                name = str(fn.get("name") or "")
                args = parse_arguments(fn.get("arguments"))
                call_id = str(call.get("id") or name)
                self._log("act", f"{name} {compact_json(args, 400)}")
                result = dispatch_tool(self.backend, name, args)
                if isinstance(result, TerminalCall):
                    terminal = result
                    tool_body = compact_json(result.payload)
                else:
                    tool_body = compact_json(result)
                    if name == "screenshot_region" and result.get("ok") and result.get("path"):
                        screenshot_path = str(result["path"])
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": tool_body,
                    }
                )
                if terminal:
                    break

            if terminal and terminal.name == "done":
                msg = terminal.payload.get("result") or "Done."
                self._log("done", msg)
                return RunResult("done", msg, step, self.backend.dry_run)
            if terminal and terminal.name == "fail":
                msg = terminal.payload.get("reason") or "Failed."
                self._log("fail", msg)
                return RunResult("fail", msg, step, self.backend.dry_run)

            if screenshot_path:
                image_msg = self._maybe_image_message(screenshot_path)
                if image_msg:
                    self._messages.append(image_msg)

            snapshot = self.backend.list_ui()
            self._log("observe", f"Window: {self._window_label(snapshot)}")
            if snapshot.get("com_error"):
                self._log("error", snapshot.get("error") or "list_ui COM failure")
            self._messages.append(
                {
                    "role": "user",
                    "content": observation_message(snapshot, step, self.max_steps),
                }
            )
            self._trim_history()

        self._log("fail", f"Reached the {self.max_steps}-step budget without done/fail.")
        return RunResult(
            "fail",
            f"Stopped after {self.max_steps} steps (budget).",
            self.max_steps,
            self.backend.dry_run,
        )

    def _trim_history(self) -> None:
        """Keep the system prompt plus a sliding window so prompts stay small."""
        if len(self._messages) <= 24:
            return
        head = self._messages[:2]
        tail = self._messages[-20:]
        self._messages = head + tail

    def _maybe_image_message(self, path: str) -> dict[str, Any] | None:
        file = Path(path)
        if not file.is_file():
            return None
        try:
            raw = file.read_bytes()
        except OSError:
            return None
        if len(raw) > 1_200_000:
            return {
                "role": "user",
                "content": f"Screenshot saved at {path} (too large to attach).",
            }
        b64 = base64.b64encode(raw).decode("ascii")
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Region screenshot attached ({file.name})."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }

    def _window_label(self, snapshot: dict[str, Any]) -> str:
        window = snapshot.get("window") or {}
        count = len(snapshot.get("controls") or [])
        err = snapshot.get("error") or window.get("error") or ""
        if snapshot.get("com_error") or "coinitialize" in str(err).lower():
            return f"COM ERROR: {err or 'UI Automation not initialized'} · {count} controls"
        launch_err = snapshot.get("launch_error")
        if launch_err:
            return f"Launch dialog: {launch_err} · {count} controls"
        name = window.get("name") or "?"
        if snapshot.get("ok") is False and err:
            return f"{name} · {count} controls (error: {err})"
        return f"{name} · {count} controls"

    def _stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    def _log(self, kind: str, message: str) -> None:
        if self.on_log:
            self.on_log(kind, message)
