# Changelog

## 0.2.4

- **navigate types the held UIA handle**: after resolving the address control, do not call generic `type_text` (which re-looks-up by automation_id/name and can return `Target control not found for type_text`). SetFocus + ValuePattern/SendKeys on that same element. If the handle is stale or typing it fails, fall through to the omnibox path (`ctrl+l`, type, Enter) inside the same call. That missing-target error never reaches the planner or live ERROR log.

## 0.2.3

- **navigate omnibox fallback**: if the Chromium address Edit/ComboBox is missing from a thin UIA tree (~10 chrome controls, common in Helium), `navigate` stays atomic — `ctrl+l`, type the URL (select-all/clear), Enter, then the existing title/address poll. It no longer returns `missing address control` and dumps the sequence on the planner.

## 0.2.2

- **navigate** (auto mode): one atomic tool to change a Chromium-family browser (Helium / Chrome / Edge) to a URL. Focuses the window (optional `title_contains` / `process_contains`), finds the address Edit/ComboBox via UIA, types the URL, presses Enter, and polls title/address until `nav_ok` or `nav_failed`. Reuses the 0.2.1 verify helpers. Other browsers return a clear failure. Prompt tells the model to call `navigate` instead of splitting `ctrl+l` / `type_text` / Enter across turns.

## 0.2.1

- **find_files** (auto mode): search the local disk (user profile, Desktop, Documents, Downloads, Program Files, Program Files (x86), Steam/common if present) by filename or glob. Returns full paths with size/mtime. The system prompt tells the model to call this first for “find/locate a file/.exe on my computer” — not Win+S into Edge, and not Explorer search-box hunting.
- **Navigation verify**: after typing a URL/query and Enter, the loop checks that the window title and/or address-bar UIA value changed toward the target (`nav_ok` / `nav_failed`). A stale title is a failure. One retry (`ctrl+l`, clear, retype, enter), then `screenshot_region`.
- **Image download discipline**: never Ctrl+S on a search/results page (that saves HTML). Prefer Save image as / a download control. New **verify_file** tool rejects HTML masquerading as `.jpg` (magic bytes / size).
- **Stuck-loop breaker**: if the same tool+args fail, or the observe snapshot fingerprint is unchanged, for 3 consecutive acts, the next planner message forces a strategy change (`find_files`, screenshot, or fail) instead of grinding to `max_steps`.

## 0.2.0-rc

- **Drag tool** (auto mode only): mouse-down `(x1,y1)` → move → up `(x2,y2)`, optional polyline `points` for rects and ellipses. Windows uses pynput; the dry-run mock records strokes. Not offered in Guide mode.
- **Canvas / thin-tree mode**: goals like sketch/draw/paint/tldraw/Figma, or a browser/whiteboard whose `list_ui` is chrome-only (~10 controls), switch the system prompt to vision + drag. The loop attaches a window screenshot after each observation on that surface. Several `drag` calls in one model turn run in order before the next snapshot (short stroke sequences, not twenty blind clicks).
- **Focus guard**: if an action leaves Desk Pilot focused, `focus_window` restores the previous target before the next plan.
- **Art goals**: `prepare_art` tries OpenRouter `/images/generations` with the same API key, then falls back to a geometric PNG + SVG. Clipboard PNG paste is Windows CF_DIB; off Windows it fails clearly and the car playbook (body rect, cabin, two wheel ellipses) is the path that works. “Sketch me a car” does **not** enable Guide mode.

## 0.1.9

- Guide sketches that sometimes painted and sometimes failed with `UpdateLayeredWindow` `GetLastError=1400` now recreate the overlay HWND on the UI thread and retry the blit once. A failed blit never leaves a stale handle on the singleton. The live log records `hwnd recreated` on the successful retry, or `hwnd recreated, still failed` if the second blit also dies.
- Overlay create/blit/hide/close still marshal to the Tk owner thread (0.1.8). Test sketch and guide both go through that owner; the agent worker never calls `UpdateLayeredWindow` itself.

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
