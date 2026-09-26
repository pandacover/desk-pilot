# Desk Pilot

Local **Windows computer-use agent**. You type a goal, Desk Pilot reads the focused window with UI Automation, captures a compressed screenshot of the content region, and runs **Apple FastVLM** (local sidecar) to extract a compact JSON scene (element boxes, labels, click centers). That **text** goes to the OpenRouter planner (`openai/gpt-6-luna` by default) — not raw images. After every action it re-reads the UI and refreshes the scene. Hard stop at 30 steps (configurable), plus a large **STOP** button.

The app window opens immediately. FastVLM loads in a background sidecar after the UI is shown (status: **Loading vision model…**, then **Vision ready**). **Run** stays disabled until the sidecar reports ready, unless you turn FastVLM off in Settings.

While FastVLM infers, the live step log heartbeats (`scene still inferring… Ns`) instead of going quiet. When `/scene` returns it logs `scene: N elements · elapsed_ms` (and **timed out** / **empty** if that happened). Sidecar file log remains `%LOCALAPPDATA%\DeskPilot\fastvlm-sidecar.log`.

On Linux and macOS the same app starts in **dry-run** mode: the desktop is a fake in-memory Windows session (Start, Run dialog, Notepad) so you can develop and CI without a Windows box. Dry-run uses a FastVLM **stub** sidecar (no PyTorch weights) so the window still opens instantly.

## Requirements

- Python 3.11 or newer
- Windows 10/11 for real control (run as Administrator if UIA cannot see some apps)
- An [OpenRouter](https://openrouter.ai/) API key
- Linux/macOS: Python Tk (`python3-tk` on Debian/Ubuntu) for the window

## Install (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-vision.txt   # FastVLM sidecar (PyTorch, transformers, timm, …)
python -m desk_pilot
```

The first FastVLM start downloads **apple/FastVLM-0.5B** from Hugging Face (~1GB) into the Hugging Face cache. Load happens in the sidecar process after the Desk Pilot window appears — the UI is not blocked. Encode uses the **native 1024px crop** (required by FastViTHD). Speed comes from greedy decode, 192 new tokens, and stopping when the scene JSON is complete — not from shrinking the image. **CUDA** is used when available; **CPU is supported**. The chip shows **Vision inferring…** while `/scene` runs. Sidecar timings (source WxH, tower WxH, `elapsed_ms`) go to `fastvlm-sidecar.log` in the Desk Pilot cache directory.

FastVLM is the default observe path. Settings **FastVLM scene observe** off (or `DESK_PILOT_FASTVLM=0`) is UIA-only for debugging, not the recommended setup. `python -m desk_pilot.vision.sidecar --stub` serves empty scenes without weights.

Or: `python run.py`

## Install (Linux dry-run)

```bash
sudo apt-get install -y python3-tk python3-venv   # Debian/Ubuntu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m desk_pilot
```

Dry-run starts a FastVLM **stub** sidecar (no PyTorch). The chip says **DRY-RUN · Vision stub**. Mouse and keyboard are not moved.

## First run

1. Open Desk Pilot (the window should appear immediately; the status chip may say **Loading vision model…**).
2. Wait until the chip says **Vision ready** (or **Vision stub** on dry-run). Run is enabled then.
3. In **Settings**, paste your OpenRouter API key (masked). Optional: override the model. Click **Save settings**.
4. Leave the goal as `Open Notepad and type hello` (or write your own).
5. Click **Run**. Confirm the dialog. Watch the step log (OBSERVE includes `scene:` JSON).
6. Hit **STOP** at any time. Esc also requests a stop.

The key is stored only in your user config:

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\DeskPilot\config.json` |
| Linux | `~/.config/desk-pilot/config.json` |
| macOS | `~/Library/Application Support/DeskPilot/config.json` |

Power users can set `OPENROUTER_API_KEY` in the environment; that **overrides** the saved key. The key is never hardcoded and must not be committed. `DESK_PILOT_FASTVLM=0` disables the sidecar; `DESK_PILOT_FASTVLM_STUB=1` forces stub scenes; `DESK_PILOT_FASTVLM_URL` overrides `http://127.0.0.1:8765`.

Region screenshots (mss crop for FastVLM, and the last-resort `screenshot_region` tool) go to `%LOCALAPPDATA%\DeskPilot\screenshots\` on Windows, or `~/.cache/desk-pilot/screenshots/` elsewhere. Sidecar logs: `fastvlm-sidecar.log` in that cache dir.

## CLI

Same loop, no window. Still needs an API key.

```bash
python -m desk_pilot --cli --goal "Open Notepad and type hello"
python -m desk_pilot --cli --mock --goal "Open Notepad and type hello"
python -m desk_pilot --cli --no-fastvlm --goal "Open Notepad and type hello"
```

`--mock` forces the dry-run desktop even on Windows.

## How it works

```
observe (UIA tree + FastVLM scene JSON) → plan (OpenRouter tools, no image) → act → observe → repeat
```

After each ACT settles, Desk Pilot captures a compressed crop of the focused window/content region (native FastVLM **1024px** crop) and POSTs it to the local FastVLM sidecar (`POST /scene`) **in parallel** with `list_ui`. Generate is greedy, capped at 192 new tokens, and stops when the scene JSON object is complete. The planner sees `scene:` compact JSON (cap ~12–20 elements, screen coords) — not a screenshot. If `/scene` still has not returned after ~40s, that step fail-opens to UIA-only as a last resort (it does **not** start a second infer).

Tools the model can call:

| Tool | Purpose |
| --- | --- |
| `list_ui` | Compact UIA tree: name, type, automation id, rect, short path |
| `click` | By automation id, name, or coordinates. `button=left` (default), `right` (context menu), or `double` |
| `drag` | Mouse-down → move → up (optional polyline). Auto mode only; for canvases |
| `prepare_art` | Sketch/draw goals: PNG (OpenRouter image or geometric) + clipboard, else drag playbook |
| `type_text` | Type into the focused or targeted control |
| `navigate` | Chromium-family address bar: focus, type a URL, Enter, verify (`nav_ok` / `nav_failed`) |
| `hotkey` | `win+r`, `enter`, `ctrl+s`, `ctrl+v`, … |
| `list_windows` / `focus_window` | Reuse an already-open app instead of launching another copy |
| `launch_app` | Start an installed app, or focus it if it is already running |
| `find_files` | Local disk search (Desktop, Documents, Downloads, Program Files, Steam). Auto mode; use this to locate a file/.exe |
| `verify_file` | Optional: after an image save, reject HTML masquerading as `.jpg` (do not spam every step) |
| `screenshot_region` | mss crop to disk; last resort. Observe already includes FastVLM scene; this does **not** send pixels to the planner |
| `wait_for_window` | Title / focus change |
| `done` / `fail` | End the run |

Default model: `openai/gpt-6-luna`. Reasoning is requested with `reasoning.effort = low` so steps stay snappy. The loop is plain Python (no LangChain / LangGraph / CrewAI).

Typical “open Notepad” path: if Notepad is already in `top_windows`, `focus_window` (or `launch_app`, which reuses). Otherwise `launch_app notepad` → `wait_for_window` → `type_text hello` → `done`.

Typical “find brawlhalla.exe” path: `find_files` with `name=brawlhalla.exe` → `done` with the full paths. Not Win+S, not Edge, not Explorer search.

The loop **already executes every tool call in one model turn**, in order, then re-reads the UI once (and refreshes the FastVLM scene). Normal goals should still emit one action. Canvas mode (below) may emit a short sequence of `drag`s in that same turn.

## Pull latest (0.2.9)

Live CUDA after 0.2.8: tower was **1024×1024** but every `/scene` was `elements=0` `FastVLM returned no JSON` in ~8s. That is an **empty generate**, not a 768 encode. 0.2.9 stops JSON early-stop from firing on the prompt, logs `n_new` + decode preview, heartbeats the UI log, and adds right-click save. Encode stays 1024.

```powershell
git pull
python -m desk_pilot
```

Windows CUDA re-run (Helium visual grid / corgi download):

1. Only **one** Desk Pilot window and **one** sidecar. `fastvlm-sidecar.log` starts with `listening on http://127.0.0.1:8765 pid=…` **before** `loading apple/FastVLM`.
2. Chip **Vision ready**. `vision tower input 1024x1024` (never 768 / 3072×0×0).
3. After Goal: `capturing scene…` then `scene still inferring… Ns`. `/scene` lines include `generate n_new=… text='{…'`. `n_new` must be **> 0** and `elements` > 0 on a normal window. If empty, the note is `FastVLM returned no JSON (n_new=N)` plus a decode preview — not a silent blank.
4. `/scene start window=` should be a real title, not `''`.
5. Acts: `navigate` → scene-click a tile **or** `button=right` → Save image as → type a Downloads `.jpg` → Save. One `save check:`. Not Ctrl+S.

## Pull latest (0.2.8)

FastVLM `/scene` was failing on every observe with `Calculated output size: (3072x0x0)` because 0.2.7 fed a **768px** crop into a tower that needs **1024px**. 768/64 = 12×12 features, then pool → 0×0, so every scene was empty. Encode is native 1024 again; latency still uses 192 tokens + JSON stop.

```powershell
git pull
python -m desk_pilot
```

Expect `scene: N elements` (N > 0 on a normal window), not `FastVLM generate failed: … (3072x0x0)`. `fastvlm-sidecar.log` should show `vision tower input 1024x1024`.

## Pull latest (0.2.7)

Makes **FastVLM `/scene` usable on CPU** (and faster on CUDA). Live 0.2.6 hung for minutes on the first generate because decode used 512 new tokens at 1024². Now: 512px CPU / 768px CUDA, greedy, 192 tokens, stop at complete JSON. Chip shows **Vision inferring…**. Sidecar log lines `elapsed_ms`. The agent no longer deadlocks by retrying `/scene` behind the sidecar lock; fail-open is last resort only. STOP still unlocks Run if a call is mid-flight.

```powershell
git pull
python -m desk_pilot
```

Expect `capturing scene…` then `scene: N elements` (or a last-resort UIA skip, not a silent hang). `fastvlm-sidecar.log` should show `/scene end elapsed_ms=…`.

## Pull latest (0.2.6)

FastVLM vision-tower extra: **timm** (plus einops / sentencepiece). Live Windows failed with `ImportError: … requires … timm`.

```powershell
git pull
pip install -r requirements-vision.txt
python -m desk_pilot
```

Expect the status chip to go **Loading vision model…** then **Vision ready** — not **Vision error**. If a package is still missing, the chip should say **Vision: missing timm** (or similar), not a generic error.

## Pull latest (0.2.5)

FastVLM scene observe. Thin-UIA canvas auto-flip and screenshot-to-LLM are gone. The planner gets FastVLM JSON, not pixels.

```powershell
git pull
pip install -r requirements.txt
pip install -r requirements-vision.txt
python -m desk_pilot
```

Expect:

1. The Desk Pilot window appears immediately. Status shows **Loading vision model…** then **Vision ready** (or **Vision stub** on Linux dry-run). Run is disabled until then.
2. OBSERVE log lines include `scene: N elements`. Planner messages contain `scene:` JSON, not `image_url`.
3. Browser goals with a thin UIA tree stay in normal mode (use scene click centers) — they must **not** auto-flip to Canvas mode. Art goals (`sketch me a car`) still use canvas + drag.
4. No CDP/Playwright.

## Pull latest (0.2.4)

`navigate` types the address-bar handle it already found (no second name/id lookup). A stale `view_*` id that used to leak `Target control not found for type_text` now falls through to `ctrl+l` inside the same tool call.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Try (auto, not Guide):

1. Any goal that needs opening a URL in Helium — expect a single `navigate` act. It must not abort with `Target control not found for type_text` or `missing address control`.
2. `find brawlhalla.exe on my computer` — expect `find_files` to return real paths in the step log. It must **not** open Edge via Win+S or type the filename into web search.
3. `download a picture of a cat` (or similar) — use FastVLM scene tiles to open an image. It must **not** Ctrl+S a results page and rename HTML to `.jpg`.

## Auto vs Guide

- **Auto** (default): a goal like `Open Helium and search for a dank meme` — the agent clicks and types. No sketch overlay. `open tldraw and sketch me a car` stays auto (it is not a how-to).
- **Guide**: a goal like `How to open Helium and search for a dank meme`, or Settings **Guide mode**. The agent sketches one control, shows an instruction, and waits. You do the click or type. Then **Continue** (or F8). STOP cancels the lesson. The live log always records `SKETCH` (rect), `SKETCH skipped`, or `SKETCH failed`. Settings **Test sketch** draws a fixed rectangle for 2 seconds so you can tell the Win32 overlay apart from targeting.

```
observe → plan one human step → sketch → you act → Continue → next step → done
```

## Canvas mode (tldraw / Figma / Paint)

UIA cannot see a whiteboard. Canvas mode turns on **only** when the goal matches sketch/draw/paint/tldraw/Figma/canvas — not because a browser tree is thin. Visual pages (image grids, photo viewers) stay in the normal loop and use FastVLM scene JSON to click.

Then Desk Pilot:

1. Biases the model to **scene JSON + `drag`**, not more `list_ui` clicks and not raw screenshots in the chat.
2. Lets the model emit several `drag`s in one turn (body rect, cabin, wheel ellipses).
3. Restores the target window if Desk Pilot steals focus after a click.
4. Prefers **create-then-insert**: `prepare_art` (OpenRouter image if the key supports `/images/generations`, else a geometric PNG/SVG). On Windows the PNG is copied to the clipboard (`ctrl+v`). If image gen or clipboard paste is unavailable, the fail is explicit and the geometric drag playbook is the fallback.

## Pull latest (0.2.0-rc)

Canvas drawing: drag tool, art-goal routing, focus guard. Thin-tree auto-mode was removed in 0.2.5 (FastVLM scene observe).

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Then try **auto** (not Guide): `open tldraw and sketch me a car`.

1. A browser should open (or focus) [tldraw.com](https://www.tldraw.com).
2. The log should say **Canvas mode**, then `drag` strokes or `prepare_art` + `ctrl+v` — not a dozen chrome clicks. OBSERVE should include FastVLM `scene:` JSON rather than attaching a screenshot to the planner.
3. If paste is unavailable, you should still get a recognizable car from a few geometric drags (body + wheels), not 20 blind clicks.
4. If Desk Pilot steals focus, the next line should restore the tldraw window.

Guide mode stays for how-to goals only (`how to open tldraw…`).

## Pull latest (0.1.9)

Hardens the sketch overlay after 0.1.8: Test sketch and guide were still flaky (`UpdateLayeredWindow` `GetLastError=1400 Invalid window handle` on some steps). Every show/hide is marshaled to the UI thread. If blit returns 1400, Desk Pilot destroys the HWND, creates a new one on that same thread, and retries once. A failed blit never keeps the stale handle.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Then **Test sketch**, then retry the how-to. You should see `SKETCH [real coords]` and the yellow outline on every Continue wait — not a mix of success and error 1400. A recovered step may log `hwnd recreated`.

## Pull latest (0.1.8)

Fixes guide-mode sketches after Test sketch works: `SKETCH failed: UpdateLayeredWindow failed (GetLastError=1400 Invalid window handle.)`. Overlay create/blit/hide now run on the UI thread. How-to steps should show the same yellow desktop outline as Test sketch.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Then **Test sketch** (still works), then retry the how-to goal. You should see `SKETCH [real coords]` and the yellow outline on the desktop while Continue waits — not error 1400.

## Pull latest (0.1.7)

Fixes two live sketch failures after 0.1.6: a useless `SKETCH [-12, -12, 12, 12]` corner box (the (0,0) ±12 fallback), and `SKETCH failed: ArgumentError: argument 2: OverflowError: int too long to convert` on 64-bit Windows (HWND/HDC/HBITMAP stuffed into 32-bit ctypes args). Overlay blit now uses pointer-sized handles. How-to steps that switch to an already-open ChatGPT/Helium/Chrome window sketch that window's rect.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Then **Test sketch**, then retry a how-to goal. You should see `SKETCH` with real desktop coordinates (for example a window `[96, 48, 1340, 880]`), not `[-12, -12, 12, 12]` and not OverflowError.

## Pull latest (0.1.6)

Fixes `SKETCH failed: AttributeError: module 'ctypes.wintypes' has no attribute 'HCURSOR'` on some Windows Python builds. Overlay create no longer depends on those missing HANDLE aliases.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Then **Test sketch**, then retry the how-to goal. You should see `SKETCH [l, t, r, b]`, not AttributeError.

## Pull latest (0.1.5)

Guide mode now logs every sketch attempt. If you still see “Your turn” with no desktop outline, look for `SKETCH skipped` or `SKETCH failed` in the live step log. Settings **Test sketch** draws a fixed rectangle for 2 seconds (overlay vs targeting).

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Then: a how-to goal (or Guide mode) should sketch the control — or explain why it did not. Use **Test sketch** first if you are unsure the overlay can paint.

## Pull latest (0.1.4)

Sketch is for how-to lessons only — not agent auto-clicks. 0.1.5 adds sketch logging and a Test sketch button.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

## Pull latest (0.1.3)

Older build: sketch flashed on auto click/type. 0.1.4 moves that overlay to Guide mode only.

## Pull latest (0.1.2)

Reuse already-open windows; OpenRouter tool-call history pairing. `git pull` then reinstall.

## Pull latest (0.1.1)

COM STA on the agent worker thread (`CoInitialize` / empty `list_ui`) and `launch_app` lookup. `git pull` then reinstall.

## Safety

- Will **not** start a run without an API key.
- Confirmation dialog before live control (can be turned off in Settings).
- Oversized red **STOP** button; Esc requests stop. If a `/scene` call is mid-flight, Run unlocks after a few seconds even if the worker is still blocked.
- Stays inside the stated goal; the system prompt tells the model not to enter passwords or pay for things unless you asked.

## Tests

```bash
python -m unittest discover -s tests -v
```

These use the dry-run desktop and a scripted fake LLM — no network, no Windows.

## Layout

```
desk_pilot/
  app/        CustomTkinter UI and local config
  agent/      observe–plan–act–verify loop and tool schemas
  desktop/    Windows UIA backend + Linux/macOS mock
  llm/        OpenRouter OpenAI-compatible client
  vision/     FastVLM sidecar (HTTP /health, /scene) + scene JSON client
```
