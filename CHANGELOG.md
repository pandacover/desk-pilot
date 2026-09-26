# Changelog

## 0.2.9

- **Empty FastVLM generations (live CUDA)**: 0.2.8 encode is fine (`vision tower input 1024x1024`) but every `/scene` returned `elements=0` with note exactly `FastVLM returned no JSON` (~7.4–8.2s). That note only happens when the decoded generate string is empty — not a schema mismatch. Cause: JSON early-stop used the **pre-expansion** prompt length. After FastViT splices image tokens, that slice included a **complete JSON example in the prompt** (and `region={...}`), so stopping fired with **0 assistant tokens** after the expensive encode. Skip-special decode of nothing is `""`. Fixes: (1) stop only on tokens after the first generate callback; (2) prompt has **no balanced JSON object**; (3) do not override `eos_token_id` (Qwen pad is `<|endoftext|>`, eos is `<|im_end|>`); (4) if the chat template drops `<image>`, insert it without throwing away the assistant prefix; (5) log `n_new` plus first chars of skip/raw decode on every `/scene`. Native 1024 crop unchanged.
- **`window=''` on `/scene`**: capture ran overlapped with `list_ui` and passed `snapshot=None`, so the sidecar never got the title. Use `focused_window_name()` on the capture path.
- **Two sidecars / two UIs**: bind HTTP **before** `load_model` (a second process used to load weights then fail on 8765). PID file + kill stale sidecar if `/health` is dead. UI pid lock refuses a second Desk Pilot window.
- **Live log keeps moving during FastVLM**: `_join_scene_worker` waited up to 40s with no UI lines after `capturing scene…`. Heartbeat every ~2.5s (`scene still inferring… Ns`) and while waiting on the planner. When `/scene` returns: `scene: N elements · elapsed_ms` plus timed out / empty.
- **Latency without shrinking encode**: do not stack a second `/scene` behind `_infer_lock`; JSON-stop every 4 *new assistant* tokens; greedy + 192 `max_new_tokens`.
- **Grounded save**: `click` `button=left|right|double`. Right-click is a general context-menu primitive. One `verify_file` after Save dialog confirm. Not a Google Images recipe. Ctrl+S still does not auto-verify.

## 0.2.8

- **FastVLM native 1024 crop**: 0.2.7 shrank encode to 512px (CPU) / 768px (CUDA) for speed. On live CUDA that produced FastViT features `(3072×12×12)` (768/64) which then pooled to `(3072×0×0)` — every `/scene` failed, every observe was `0 elements`. Restore Apple's **1024²** crop. Keep greedy decode, 192 new tokens, and JSON early-stop for latency. Upscale (never downscale) if `pixel_values` spatial size is below 1024. Log source WxH and vision-tower WxH on each `/scene`.

## 0.2.7

- **FastVLM `/scene` latency**: CPU generate was using a 1024² crop and `max_new_tokens=512` (Apple's own snippet uses 128). That decode is what hung live Windows for minutes after Vision ready. Sidecar now downscales to **512px on CPU / 768px on CUDA**, greedy decode, **192 new tokens**, and **stops when the JSON object is complete**. Decode only the new tokens (not the prompt example JSON). Optional CUDA 8-bit via `DESK_PILOT_FASTVLM_8BIT=1` if bitsandbytes imports. Expected: CUDA ~1–3s/scene after warmup; CPU ~5–15s after warmup (first call slower). Sidecar logs `/scene start` and `/scene end elapsed_ms=…` to `fastvlm-sidecar.log`.
- **`/health` progress**: `status` is `loading|ready|busy|error` with `phase` `loading_weights|inferring`. The chip shows **Vision inferring…** during generate instead of a silent hang. Run stays enabled while busy (model is already loaded).
- **Agent deadlock**: `_observe_pair` must not start a second `/scene` after join timeout (that stacked behind `_infer_lock`). Fail-open to UIA is a **last resort** after the tuned timeout, logged as `FastVLM last-resort skip`.
- **STOP unsticks `_running`**: observe honors `stop_event`; if the worker is still alive ~2.5s after STOP, the UI abandons the stuck step so Run works again.

## 0.2.6

- **FastVLM `timm`**: `apple/FastVLM-0.5B` remote code imports `timm` for the vision tower. Live Windows load failed with `ImportError: This modeling file requires ... timm`. `requirements-vision.txt` now includes `timm`, plus `einops` and `sentencepiece` (the other easy-to-miss extras from Apple's FastVLM pyproject). Reinstall vision deps, then restart Desk Pilot.
- **Clearer vision error chip**: if `/health` reports a missing package, the status shows `Vision: missing timm` instead of a generic **Vision error**.

## 0.2.5

- **FastVLM scene observe**: after each ACT, capture a compressed crop of the focused window/content region and POST it to a local FastVLM sidecar (`apple/FastVLM-0.5B`). OBSERVE includes UIA plus compact `scene:` JSON (element boxes, labels, click centers; cap 20). The OpenRouter planner does **not** receive raw screenshots by default. Scene inference overlaps `list_ui` so it is ready before PLAN.
- **UI does not wait on model load**: the window opens immediately; the sidecar starts afterward. Status shows **Loading vision model…** until `GET /health` is `ready`. **Run** stays disabled until then (or until FastVLM is turned off in Settings / `DESK_PILOT_FASTVLM=0`). Torch stays in the sidecar process.
- **Removed thin-UIA canvas auto-flip** that forced vision+drag on browser goals. Canvas mode is art/sketch/draw goals only. Visual/thin trees use FastVLM scene in the normal loop.
- **Removed screenshot-to-LLM** (canvas window attach and `screenshot_region` image dumps). `screenshot_region` remains as a disk crop last resort.
- **No verify_file spam / Google Images recipes** in the loop. `verify_file` stays an optional tool. No CDP/Playwright.
- **Sidecar**: `python -m desk_pilot.vision.sidecar` — `GET /health` (`loading|ready|error`), `POST /scene`. `--stub` for dry-run/CI without weights. Optional `requirements-vision.txt`. CPU is OK; first load is slow.

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
