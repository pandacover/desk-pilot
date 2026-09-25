# Changelog

## 0.1.1

- Initialize COM (STA `CoInitializeEx`) on the agent worker thread and CLI before any UI Automation call. Recreate `uiautomation`’s process-wide COM singleton on that thread so CustomTkinter’s background run no longer returns empty trees (`Window: ? · 0 controls`) with `CoInitialize has not been called`.
- Surface COM failures in the live OBSERVE log instead of a silent empty window.
- Add `launch_app` (PATH, App Paths, Start Menu `.lnk`, `shell:AppsFolder`). Win+R “Windows cannot find” dialogs are detected; `wait_for_window` will not treat that dialog as the target app.
