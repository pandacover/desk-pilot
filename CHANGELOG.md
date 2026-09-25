# Changelog

## 0.1.8

- Guide sketches were failing with `UpdateLayeredWindow` `GetLastError=1400` (invalid window handle) after Test sketch worked. The overlay HWND is now owned by the Tk UI thread; `show`/`hide`/`close` from the agent worker are marshaled onto that thread. A HWND created on another thread is never blit from the worker.

## 0.1.7

- Do not sketch the (0,0) ±12 placeholder (`[-12,-12,12,12]`). `find_control_rect` rejects origin coordinates; overlay/guide log `SKETCH skipped` with a reason instead of painting a corner box. Switching to an already-open Chrome/Helium/ChatGPT window uses `list_windows` title match and sketches that window's bounds.
- Fix x64 `OverflowError: int too long to convert` in overlay blit: SetWindowPos / UpdateLayeredWindow / GetDC / SelectObject / CreateDIBSection now have pointer-sized `HWND`/`HDC`/`HBITMAP` argtypes. HWND_TOPMOST is a HANDLE, not a 32-bit int. Absurd rects are discarded before blit.

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
