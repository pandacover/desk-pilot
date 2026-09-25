# Changelog

## 0.1.6

- Fix Win32 sketch overlay: some Python builds lack `ctypes.wintypes.HCURSOR` (and similar HANDLE aliases). Overlay create now maps those to `HANDLE` / `c_void_p` instead of raising `AttributeError`. Test sketch and guide steps can paint again.

## 0.1.5

- Guide sketch is no longer silent: the live log always prints `SKETCH` with the rect, `SKETCH skipped: no rect (need name/automation_id/xy)`, or `SKETCH failed:` plus the Win32/`GetLastError` (or exception). Overlay create/blit errors are not swallowed.
- `guide_step` must include `automation_id`, `name`, or `x,y` from the latest `list_ui`. Instruction-only calls are rejected so the model retries. Name lookup is case-insensitive contains; if the control is still missing, the focused (or titled) window is sketched as a fallback.
- Settings **Test sketch** draws a fixed rectangle for 2 seconds so you can tell a Win32 overlay failure apart from a targeting miss.
- Rects accept list or tuple. The overlay stays up until Continue, then hides.

## 0.1.4

- Guide / how-to mode: goals like “how to …” or Settings **Guide mode** sketch each target and wait for you (Continue / F8). The agent does not click or type those steps. Auto goals have no overlay.

## 0.1.3

- Sketch overlay around UIA targets (replaced in 0.1.4: overlay is guide-only, not auto-act).

## 0.1.2

- Reuse an already-open app: snapshots include `top_windows`; new `list_windows` / `focus_window` tools; `launch_app` focuses a matching window (`reused=true`) instead of starting a second instance.
- Fix OpenRouter HTTP 400 `No tool call found for function call output with call_id …` by pairing tool results with assistant `tool_calls` (sanitize + turn-based trim) and resetting history once if that error still appears.

## 0.1.1

- Initialize COM (STA `CoInitializeEx`) on the agent worker thread and CLI before any UI Automation call. Recreate `uiautomation`’s process-wide COM singleton on that thread so CustomTkinter’s background run no longer returns empty trees (`Window: ? · 0 controls`) with `CoInitialize has not been called`.
- Surface COM failures in the live OBSERVE log instead of a silent empty window.
- Add `launch_app` (PATH, App Paths, Start Menu `.lnk`, `shell:AppsFolder`). Win+R “Windows cannot find” dialogs are detected; `wait_for_window` will not treat that dialog as the target app.
