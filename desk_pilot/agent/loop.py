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
    CANVAS_SYSTEM_PROMPT,
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
    AUTO_ONLY_TOOLS,
    GUIDE_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TerminalCall,
    compact_json,
    dispatch_tool,
    parse_arguments,
)
from desk_pilot.agent.nav import looks_like_navigation, nav_fingerprint, verify_enter_navigation
from desk_pilot.agent.stuck import StuckTracker, snapshot_fingerprint
from desk_pilot.desktop.base import DesktopBackend
from desk_pilot.desktop.files import is_image_download_goal, looks_like_image_path, verify_file
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
    canvas_mode: bool = False
    _target_title: str = ""
    _target_process: str = ""

    def run(self, goal: str) -> RunResult:
        goal = (goal or "").strip()
        if not goal:
            return RunResult("fail", "Enter a goal first.", 0, self.backend.dry_run)

        self._goal = goal
        self.guide_mode = is_guide_goal(goal, force=self.force_guide)
        from desk_pilot.agent.canvas import is_art_goal, should_use_canvas_mode

        self.canvas_mode = (not self.guide_mode) and is_art_goal(goal)
        self._target_title = ""
        self._target_process = ""
        self._stuck = StuckTracker()
        self._pending_nav_text: str | None = None
        self._pending_save_path: str | None = None
        self._log("info", f"Goal: {goal}")
        if self.guide_mode:
            self._log(
                "guide",
                "Guide mode: sketch each step, you act. Click Continue (or F8) when done. STOP cancels.",
            )
        elif self.canvas_mode:
            self._log(
                "info",
                "Canvas mode: art goal — vision + drag (prepare_art / geometric playbook). Guide stays off.",
            )
        if self.backend.dry_run:
            self._log("info", "Dry-run desktop: actions are simulated, not sent to the OS.")

        snapshot = self.backend.list_ui()
        self._last_snapshot = snapshot
        self._remember_target(snapshot)
        self._stuck.last_fp = snapshot_fingerprint(snapshot)
        if (not self.guide_mode) and should_use_canvas_mode(goal, snapshot):
            self._enable_canvas("thin UIA tree or art/canvas window")
        system = self._system_prompt()
        self._messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": user_goal_message(
                    goal, snapshot, self.backend.dry_run, guide=self.guide_mode, canvas=self.canvas_mode
                ),
            },
        ]
        self._log("observe", f"Window: {self._window_label(snapshot)}")
        if snapshot.get("com_error"):
            self._log("error", snapshot.get("error") or "list_ui COM failure")
        self._attach_canvas_screenshot(snapshot)

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
                        "drag, prepare_art, an action, wait_for_window, done, or fail."
                        if self.canvas_mode
                        else (
                            "You must call a tool: list_windows, focus_window, launch_app, "
                            "find_files, verify_file, navigate, an action, wait_for_window, done, or fail."
                        )
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
            turn_events: list[dict[str, Any]] = []
            for call in tool_calls:
                if self._stopped():
                    self._clear_guide()
                    return RunResult("stopped", "Stopped by user.", step, self.backend.dry_run)
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                name = str(fn.get("name") or "")
                args = parse_arguments(fn.get("arguments"))
                call_id = str(call.get("id") or "")
                if self.guide_mode and name in AUTO_ONLY_TOOLS:
                    result = {
                        "ok": False,
                        "guide": True,
                        "error": f"{name} is auto-mode only. In guide mode call guide_step.",
                    }
                    self._messages.append(
                        {"role": "tool", "tool_call_id": call_id, "content": compact_json(result)}
                    )
                    continue
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
                result = self._dispatch_action(name, args)
                if isinstance(result, TerminalCall):
                    terminal = result
                    tool_body = compact_json(result.payload)
                    turn_events.append({"name": name, "args": args, "ok": True})
                else:
                    if name not in {"list_ui", "list_windows", "focus_window", "done", "fail"}:
                        restored = self._restore_focus_if_stolen()
                        if restored and isinstance(result, dict):
                            result = dict(result)
                            result["focus_guard"] = restored
                    tool_body = compact_json(result)
                    if name == "screenshot_region" and result.get("ok") and result.get("path"):
                        screenshot_path = str(result["path"])
                    if isinstance(result, dict) and result.get("screenshot") and not screenshot_path:
                        screenshot_path = str(result["screenshot"])
                    if isinstance(result, dict) and result.get("nav_failed"):
                        self._log("error", result.get("reason") or "nav_failed")
                    self._remember_target_from_result(name, result)
                    turn_events.append(
                        {
                            "name": name,
                            "args": args,
                            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
                        }
                    )
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
            self._remember_target(snapshot)
            from desk_pilot.agent.canvas import art_subject, playbook_hint, should_use_canvas_mode

            if (not self.guide_mode) and should_use_canvas_mode(getattr(self, "_goal", ""), snapshot):
                self._enable_canvas("thin UIA tree after action")
            self._log("observe", f"Window: {self._window_label(snapshot)}")
            if snapshot.get("com_error"):
                self._log("error", snapshot.get("error") or "list_ui COM failure")
            playbook = ""
            if self.canvas_mode:
                playbook = playbook_hint(art_subject(getattr(self, "_goal", "")), self._window_rect())
            notice = ""
            if (not self.guide_mode) and turn_events and self._stuck.note(turn_events, snapshot):
                notice = self._stuck.message()
                self._log("error", notice.split(".")[0] + ".")
            self._messages.append(
                {
                    "role": "user",
                    "content": observation_message(
                        snapshot,
                        step,
                        self.max_steps,
                        guide=self.guide_mode,
                        canvas=self.canvas_mode,
                        playbook=playbook,
                        notice=notice,
                    ),
                }
            )
            if not screenshot_path:
                self._attach_canvas_screenshot(snapshot)
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
                if sketch.get("recreated"):
                    extra += " hwnd recreated"
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
        system = self._system_prompt()
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
                "content": user_goal_message(
                    goal, snapshot, self.backend.dry_run, guide=self.guide_mode, canvas=self.canvas_mode
                )
                + extra,
            },
        ]

    def _system_prompt(self) -> str:
        if self.guide_mode:
            return GUIDE_SYSTEM_PROMPT
        if self.canvas_mode:
            return CANVAS_SYSTEM_PROMPT
        return SYSTEM_PROMPT

    def _dispatch_action(self, name: str, args: dict[str, Any]) -> dict[str, Any] | TerminalCall:
        extra = {
            "llm": self.llm,
            "window_rect": self._window_rect(),
            "log": self._log,
        }
        keys = str(args.get("keys") or "").strip().lower().replace(" ", "")
        if name == "type_text":
            text = str(args.get("text") or "")
            snap = getattr(self, "_last_snapshot", None)
            if looks_like_navigation(text, snap if isinstance(snap, dict) else None):
                self._pending_nav_text = text
            if looks_like_image_path(text):
                self._pending_save_path = text.strip().strip('"')
            return dispatch_tool(self.backend, name, args, extra=extra)
        if (
            name == "hotkey"
            and keys in {"enter", "return"}
            and getattr(self, "_pending_nav_text", None)
            and not self.guide_mode
        ):
            before = nav_fingerprint(getattr(self, "_last_snapshot", None) if isinstance(getattr(self, "_last_snapshot", None), dict) else None)
            try:
                before = nav_fingerprint(self.backend.list_ui(max_depth=3, max_controls=40))
            except Exception:
                pass
            result = dispatch_tool(self.backend, name, args, extra=extra)
            if isinstance(result, TerminalCall):
                return result
            checked = verify_enter_navigation(
                self.backend,
                str(self._pending_nav_text),
                before,
                result if isinstance(result, dict) else {"ok": bool(result)},
                log=self._log,
            )
            self._pending_nav_text = None
            return self._maybe_verify_saved_image(checked, keys)
        result = dispatch_tool(self.backend, name, args, extra=extra)
        if isinstance(result, TerminalCall):
            return result
        if name == "hotkey" and keys in {"ctrl+s"}:
            result = self._annotate_ctrl_s(result if isinstance(result, dict) else {"ok": bool(result)})
        if name == "hotkey" and keys in {"enter", "return", "ctrl+s"}:
            result = self._maybe_verify_saved_image(
                result if isinstance(result, dict) else {"ok": bool(result)},
                keys,
            )
        return result

    def _annotate_ctrl_s(self, result: dict[str, Any]) -> dict[str, Any]:
        payload = dict(result)
        if is_image_download_goal(getattr(self, "_goal", "")):
            warning = (
                "ctrl+s on a search/results page saves HTML, not an image. "
                "Open the image and use Save image as / a download control, then verify_file."
            )
            payload["warning"] = warning
            self._log("act", warning)
        return payload

    def _maybe_verify_saved_image(self, result: dict[str, Any], keys: str) -> dict[str, Any]:
        path = getattr(self, "_pending_save_path", None)
        if not path:
            return result
        if keys not in {"enter", "return", "ctrl+s"}:
            return result
        if not (is_image_download_goal(getattr(self, "_goal", "")) or looks_like_image_path(path)):
            return result
        checked = verify_file(path, expect="image")
        payload = dict(result)
        payload["verify_file"] = checked
        if not checked.get("ok"):
            payload["ok"] = False
            payload["error"] = checked.get("error") or "Saved file is not a real image."
            self._log("error", payload["error"])
        self._pending_save_path = None
        return payload

    def _enable_canvas(self, reason: str) -> None:
        if self.guide_mode or self.canvas_mode:
            return
        self.canvas_mode = True
        self._log("info", f"Canvas mode: {reason} — vision + drag.")
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0] = {"role": "system", "content": CANVAS_SYSTEM_PROMPT}

    def _window_rect(self) -> list[int] | None:
        from desk_pilot.agent.canvas import window_rect_from_snapshot

        snap = self._last_snapshot if isinstance(getattr(self, "_last_snapshot", None), dict) else {}
        return window_rect_from_snapshot(snap)

    def _remember_target(self, snapshot: dict[str, Any] | None) -> None:
        from desk_pilot.desktop.launch import is_agent_window

        window = (snapshot or {}).get("window") if isinstance((snapshot or {}).get("window"), dict) else {}
        name = str((window or {}).get("name") or "")
        process = str((window or {}).get("process") or "")
        if name and not is_agent_window(name, process):
            self._target_title = name
            self._target_process = process

    def _remember_target_from_result(self, name: str, result: dict[str, Any] | None) -> None:
        from desk_pilot.desktop.launch import is_agent_window

        if not isinstance(result, dict) or not result.get("ok"):
            return
        if name not in {"focus_window", "launch_app", "wait_for_window", "navigate"}:
            return
        title = str(result.get("window") or result.get("title") or "")
        process = str(result.get("process") or "")
        if title and not is_agent_window(title, process):
            self._target_title = title
            if process:
                self._target_process = process

    def _restore_focus_if_stolen(self) -> dict[str, Any] | None:
        from desk_pilot.desktop.launch import is_agent_window

        if self.guide_mode:
            return None
        title = (self._target_title or "").strip()
        if not title:
            return None
        try:
            snap = self.backend.list_ui(max_depth=2, max_controls=24)
        except Exception:
            return None
        window = snap.get("window") if isinstance(snap.get("window"), dict) else {}
        name = str((window or {}).get("name") or "")
        process = str((window or {}).get("process") or "")
        if not is_agent_window(name, process):
            self._remember_target(snap)
            return None
        result = self.backend.focus_window(
            title_contains=title,
            process_contains=self._target_process or None,
        )
        self._log("act", f"focus guard: Desk Pilot had focus; restored {title!r}")
        return result if isinstance(result, dict) else {"ok": bool(result), "window": title}

    def _attach_canvas_screenshot(self, snapshot: dict[str, Any] | None) -> None:
        from desk_pilot.agent.canvas import is_browser_or_whiteboard, window_rect_from_snapshot

        if self.guide_mode or not self.canvas_mode:
            return
        if not snapshot:
            return
        if not is_browser_or_whiteboard(snapshot):
            return
        box = window_rect_from_snapshot(snapshot)
        if not box:
            return
        left, top, right, bottom = box
        width, height = max(1, right - left), max(1, bottom - top)
        try:
            captured = self.backend.screenshot_region(left, top, width, height)
        except Exception as exc:  # noqa: BLE001
            self._log("error", f"canvas screenshot failed: {exc}")
            return
        if not (captured.get("ok") and captured.get("path")):
            return
        image_msg = self._maybe_image_message(
            str(captured["path"]),
            caption="Canvas screenshot attached. UIA is thin; plan drag strokes from this image.",
        )
        if image_msg:
            self._messages.append(image_msg)

    def _maybe_image_message(self, path: str, caption: str | None = None) -> dict[str, Any] | None:
        file = Path(path)
        if not file.is_file():
            return None
        try:
            raw = file.read_bytes()
        except OSError:
            return None
        label = caption or f"Region screenshot attached ({file.name})."
        if len(raw) > 1_200_000:
            return {
                "role": "user",
                "content": f"Screenshot saved at {path} (too large to attach).",
            }
        b64 = base64.b64encode(raw).decode("ascii")
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": label},
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
