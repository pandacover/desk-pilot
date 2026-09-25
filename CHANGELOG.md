# Changelog

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
