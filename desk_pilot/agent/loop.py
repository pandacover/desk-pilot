from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from desk_pilot.agent.guide import (
    ACTION_TOOLS,
    FALLBACK_WINDOW_NOTE,
    expected_from_args,
    guide_has_locator,
    instruction_for_tool,
    is_guide_goal,
    resolve_guide_rect,
    snapshot_advanced,
)
from desk_pilot.agent.prompts import (
    GUIDE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    observation_message,
    user_goal_message,
)
from desk_pilot.agent.history import (
    is_tool_pairing_error,
    normalize_tool_calls,
    sanitize_messages,
    trim_messages,
)
from desk_pilot.agent.tools import (
    GUIDE_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TerminalCall,
    compact_json,
    dispatch_tool,
    parse_arguments,
)
from desk_pilot.desktop.base import DesktopBackend
from desk_pilot.llm.openrouter import LLMClient, LLMError


LogFn = Callable[[str, str], None]
GuideFn = Callable[[str], None]
AwaitFn = Callable[[], str]


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
    force_guide: bool = False
    continue_event: threading.Event | None = None
    on_guide_step: GuideFn | None = None
    await_step: AwaitFn | None = None
    _messages: list[dict[str, Any]] = field(default_factory=list)
    guide_mode: bool = False

    def run(self, goal: str) -> RunResult:
        goal = (goal or "").strip()
        if not goal:
            return RunResult("fail", "Enter a goal first.", 0, self.backend.dry_run)

        self._goal = goal
        self.guide_mode = is_guide_goal(goal, force=self.force_guide)
        self._log("info", f"Goal: {goal}")
        if self.guide_mode:
            self._log(
                "guide",
                "Guide mode: sketch each step, you act. Click Continue (or F8) when done. STOP cancels.",
            )
        if self.backend.dry_run:
            self._log("info", "Dry-run desktop: actions are simulated, not sent to the OS.")

        snapshot = self.backend.list_ui()
        self._last_snapshot = snapshot
        system = GUIDE_SYSTEM_PROMPT if self.guide_mode else SYSTEM_PROMPT
        self._messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": user_goal_message(goal, snapshot, self.backend.dry_run, guide=self.guide_mode),
            },
        ]
        self._log("observe", f"Window: {self._window_label(snapshot)}")
        if snapshot.get("com_error"):
            self._log("error", snapshot.get("error") or "list_ui COM failure")

        for step in range(1, self.max_steps + 1):
            if self._stopped():
                self._clear_guide()
                return RunResult("stopped", "Stopped by user.", step - 1, self.backend.dry_run)
            self._log("plan", f"Step {step}/{self.max_steps} — asking the model…")
            try:
                response = self._complete()
            except LLMError as exc:
                self._log("error", str(exc))
                self._clear_guide()
                return RunResult("fail", str(exc), step, self.backend.dry_run)
            except Exception as exc:  # noqa: BLE001 — surface unexpected LLM failures
                self._log("error", f"LLM error: {exc}")
                self._clear_guide()
                return RunResult("fail", f"LLM error: {exc}", step, self.backend.dry_run)

            tool_calls = normalize_tool_calls(response.get("tool_calls") or [])
            content = (response.get("content") or "").strip()
            if content:
                self._log("plan", content[:400])

            if not tool_calls:
                nudge = (
                    "You must call guide_step (one human step), done, or fail."
                    if self.guide_mode
                    else (
                        "You must call a tool: list_windows, focus_window, launch_app, "
                        "an action, wait_for_window, done, or fail."
                    )
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
            guided = False
            for call in tool_calls:
                if self._stopped():
                    self._clear_guide()
                    return RunResult("stopped", "Stopped by user.", step, self.backend.dry_run)
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                name = str(fn.get("name") or "")
                args = parse_arguments(fn.get("arguments"))
                call_id = str(call.get("id") or "")
                if self.guide_mode and (name == "guide_step" or name in ACTION_TOOLS):
                    result = self._run_guide_step(name, args)
                    guided = True
                    tool_body = compact_json(result)
                    self._log("guide", result.get("instruction") or name)
                    self._messages.append(
                        {"role": "tool", "tool_call_id": call_id, "content": tool_body}
                    )
                    if result.get("awaited") == "stopped":
                        self._clear_guide()
                        return RunResult("stopped", "Stopped by user.", step, self.backend.dry_run)
                    break
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
                        "content": tool_body,
                    }
                )
                if terminal:
                    break

            if terminal and terminal.name == "done":
                msg = terminal.payload.get("result") or "Done."
                self._log("done", msg)
                self._clear_guide()
                return RunResult("done", msg, step, self.backend.dry_run)
            if terminal and terminal.name == "fail":
                msg = terminal.payload.get("reason") or "Failed."
                self._log("fail", msg)
                self._clear_guide()
                return RunResult("fail", msg, step, self.backend.dry_run)

            if screenshot_path:
                image_msg = self._maybe_image_message(screenshot_path)
                if image_msg:
                    self._messages.append(image_msg)

            snapshot = self.backend.list_ui()
            self._last_snapshot = snapshot
            self._log("observe", f"Window: {self._window_label(snapshot)}")
            if snapshot.get("com_error"):
                self._log("error", snapshot.get("error") or "list_ui COM failure")
            self._messages.append(
                {
                    "role": "user",
                    "content": observation_message(
                        snapshot, step, self.max_steps, guide=self.guide_mode
                    ),
                }
            )
            self._messages = trim_messages(sanitize_messages(self._messages))
            if guided:
                continue

        self._log("fail", f"Reached the {self.max_steps}-step budget without done/fail.")
        self._clear_guide()
        return RunResult(
            "fail",
            f"Stopped after {self.max_steps} steps (budget).",
            self.max_steps,
            self.backend.dry_run,
        )

    def _run_guide_step(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        from desk_pilot.desktop.rects import SKIP_NO_RECT, rect_skip_reason, sketchable_rect

        if name == "guide_step":
            payload = dispatch_tool(self.backend, "guide_step", args)
            if isinstance(payload, TerminalCall):
                return {"ok": False, "error": "guide_step is not terminal."}
            if payload.get("skip"):
                self._log("sketch", f"skipped: {SKIP_NO_RECT}")
                payload = dict(payload)
                payload["awaited"] = "skipped"
                payload["acted"] = False
                return payload
        else:
            resolved = resolve_guide_rect(self.backend, args, tool_name=name)
            instruction = instruction_for_tool(name, args)
            if resolved.get("fallback") and resolved.get("rect"):
                instruction = f"{instruction} {FALLBACK_WINDOW_NOTE}".strip()
            payload = {
                "ok": True,
                "guide": True,
                "instruction": instruction,
                "rect": resolved.get("rect"),
                "source": resolved.get("source"),
                "fallback": bool(resolved.get("fallback")),
                "intercepted": name,
                "note": "Did not perform this action; waiting for you.",
                "error": resolved.get("error"),
            }

        instruction = str(payload.get("instruction") or instruction_for_tool(name, args))
        box = sketchable_rect(payload.get("rect"))
        expected = expected_from_args(name, args)
        if payload.get("expected_title") and not expected:
            expected = {"title_contains": str(payload["expected_title"])}

        attempted = False
        shown = False
        if box:
            attempted = True
            try:
                sketch = self.backend.show_highlight(box)
            except Exception as exc:  # noqa: BLE001 — overlay errors must reach the log
                sketch = {
                    "ok": False,
                    "skipped": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "rect": box,
                }
            if not isinstance(sketch, dict):
                sketch = {
                    "ok": bool(sketch),
                    "skipped": False,
                    "error": None if sketch else "show_highlight returned no result",
                    "rect": box,
                }
            if sketch.get("ok"):
                extra = " window fallback" if payload.get("fallback") else ""
                self._log("sketch", f"{box}{extra}")
                shown = True
            elif sketch.get("skipped"):
                self._log("sketch", f"skipped: {sketch.get('error') or SKIP_NO_RECT}")
            else:
                self._log("sketch", f"failed: {sketch.get('error') or 'overlay error'}")
        else:
            self._log("sketch", f"skipped: {rect_skip_reason(payload.get('rect')) or payload.get('error') or SKIP_NO_RECT}")
            if name == "guide_step" and not guide_has_locator(args):
                payload = dict(payload)
                payload["instruction"] = instruction
                payload["awaited"] = "skipped"
                payload["acted"] = False
                payload["rect"] = None
                return payload
            # Intercepted action with no rect: still wait so the user can Continue.

        if self.on_guide_step:
            self.on_guide_step(instruction)
        awaited = self._await_user(expected)
        if attempted:
            try:
                self.backend.hide_highlight()
            except Exception as exc:  # noqa: BLE001
                self._log("sketch", f"failed: hide {exc}")
        if self.on_guide_step:
            self.on_guide_step("")
        payload = dict(payload)
        payload["instruction"] = instruction
        payload["awaited"] = awaited
        payload["acted"] = False
        payload["rect"] = box
        payload["sketched"] = shown
        return payload

    def _await_user(self, expected: dict[str, str] | None) -> str:
        if self.await_step is not None:
            return self.await_step() or "continue"
        if self.continue_event is None:
            return "continue"
        self.continue_event.clear()
        started = time.time()
        before = self._last_snapshot if isinstance(getattr(self, "_last_snapshot", None), dict) else {}
        while True:
            if self._stopped():
                return "stopped"
            if self.continue_event.is_set():
                return "continue"
            if time.time() - started >= 0.7:
                try:
                    after = self.backend.list_ui()
                except Exception:
                    after = before
                if snapshot_advanced(before, after, expected):
                    self._last_snapshot = after
                    self._log("guide", "Detected the UI change — advancing.")
                    return "detected"
            time.sleep(0.2)

    def _clear_guide(self) -> None:
        try:
            self.backend.hide_highlight()
        except Exception:
            pass
        if self.on_guide_step:
            self.on_guide_step("")

    def _complete(self) -> dict[str, Any]:
        last_error: LLMError | None = None
        tools = GUIDE_TOOL_DEFINITIONS if self.guide_mode else [
            item for item in TOOL_DEFINITIONS if item["function"]["name"] != "guide_step"
        ]
        for attempt in range(2):
            payload = sanitize_messages(self._messages)
            try:
                return self.llm.complete(payload, tools)
            except LLMError as exc:
                last_error = exc
                if not is_tool_pairing_error(exc):
                    raise
                self._log("error", f"{exc} Resetting tool-call history and retrying.")
                self._reset_history()
        raise last_error or LLMError("OpenRouter request failed.")

    def _reset_history(self) -> None:
        snapshot = self._last_snapshot if isinstance(getattr(self, "_last_snapshot", None), dict) else {}
        goal = getattr(self, "_goal", "") or ""
        system = GUIDE_SYSTEM_PROMPT if self.guide_mode else SYSTEM_PROMPT
        extra = (
            "\n\nTool-call history was reset after an API pairing error. "
            "Continue from this UI. If the target app is already in top_windows, "
        )
        extra += (
            "guide the user to that window; do not launch a second copy."
            if self.guide_mode
            else "focus_window it; do not launch a second copy."
        )
        self._messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": user_goal_message(goal, snapshot, self.backend.dry_run, guide=self.guide_mode)
                + extra,
            },
        ]

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
