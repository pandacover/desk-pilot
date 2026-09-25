# Desk Pilot

Local **Windows computer-use agent**. You type a goal, Desk Pilot reads the focused window with UI Automation, plans with an OpenRouter LLM (`openai/gpt-6-luna` by default), then clicks and types. After every action it re-reads the UI. Hard stop at 30 steps (configurable), plus a large **STOP** button.

On Linux and macOS the same app starts in **dry-run** mode: the desktop is a fake in-memory Windows session (Start, Run dialog, Notepad) so you can develop and CI without a Windows box.

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
python -m desk_pilot
```

Or: `python run.py`

## Install (Linux dry-run)

```bash
sudo apt-get install -y python3-tk python3-venv   # Debian/Ubuntu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m desk_pilot
```

The status chip will say **DRY-RUN**. Mouse and keyboard are not moved.

## First run

1. Open Desk Pilot.
2. In **Settings**, paste your OpenRouter API key (masked). Optional: override the model. Click **Save settings**.
3. Leave the goal as `Open Notepad and type hello` (or write your own).
4. Click **Run**. Confirm the dialog. Watch the step log.
5. Hit **STOP** at any time. Esc also requests a stop.

The key is stored only in your user config:

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\DeskPilot\config.json` |
| Linux | `~/.config/desk-pilot/config.json` |
| macOS | `~/Library/Application Support/DeskPilot/config.json` |

Power users can set `OPENROUTER_API_KEY` in the environment; that **overrides** the saved key. The key is never hardcoded and must not be committed.

Region screenshots (mss, only when UIA cannot find a control) go to `%LOCALAPPDATA%\DeskPilot\screenshots\` on Windows, or `~/.cache/desk-pilot/screenshots/` elsewhere.

## CLI

Same loop, no window. Still needs an API key.

```bash
python -m desk_pilot --cli --goal "Open Notepad and type hello"
python -m desk_pilot --cli --mock --goal "Open Notepad and type hello"
```

`--mock` forces the dry-run desktop even on Windows.

## How it works

```
observe (UIA tree) → plan (OpenRouter tools) → act (click / type / hotkey / drag) → verify → repeat
```

Tools the model can call:

| Tool | Purpose |
| --- | --- |
| `list_ui` | Compact UIA tree: name, type, automation id, rect, short path |
| `click` | By automation id, name, or coordinates |
| `drag` | Mouse-down → move → up (optional polyline). Auto mode only; for canvases |
| `prepare_art` | Sketch/draw goals: PNG (OpenRouter image or geometric) + clipboard, else drag playbook |
| `type_text` | Type into the focused or targeted control |
| `hotkey` | `win+r`, `enter`, `ctrl+s`, `ctrl+v`, … |
| `list_windows` / `focus_window` | Reuse an already-open app instead of launching another copy |
| `launch_app` | Start an installed app, or focus it if it is already running |
| `find_files` | Local disk search (Desktop, Documents, Downloads, Program Files, Steam). Auto mode; use this to locate a file/.exe |
| `verify_file` | After an image save: reject HTML masquerading as `.jpg` |
| `screenshot_region` | mss crop; last resort except in canvas mode |
| `wait_for_window` | Title / focus change |
| `done` / `fail` | End the run |

Default model: `openai/gpt-6-luna`. Reasoning is requested with `reasoning.effort = low` so steps stay snappy. The loop is plain Python (no LangChain / LangGraph / CrewAI).

Typical “open Notepad” path: if Notepad is already in `top_windows`, `focus_window` (or `launch_app`, which reuses). Otherwise `launch_app notepad` → `wait_for_window` → `type_text hello` → `done`.

Typical “find brawlhalla.exe” path: `find_files` with `name=brawlhalla.exe` → `done` with the full paths. Not Win+S, not Edge, not Explorer search.

The loop **already executes every tool call in one model turn**, in order, then re-reads the UI once. Normal goals should still emit one action. Canvas mode (below) may emit a short sequence of `drag`s in that same turn.

## Pull latest (0.2.1)

Finds files on disk, verifies browser navigation actually happened, and stops grinding a stuck Explorer/search loop.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Try (auto, not Guide):

1. `find brawlhalla.exe on my computer` — expect `find_files` to return real paths in the step log. It must **not** open Edge via Win+S or type the filename into web search.
2. `download a picture of a cat` (or similar) — it must **not** Ctrl+S a Google/Images results page and rename HTML to `.jpg`. Open the image, Save image as / download control, then `verify_file`.

## Auto vs Guide

- **Auto** (default): a goal like `Open Helium and search for a dank meme` — the agent clicks and types. No sketch overlay. `open tldraw and sketch me a car` stays auto (it is not a how-to).
- **Guide**: a goal like `How to open Helium and search for a dank meme`, or Settings **Guide mode**. The agent sketches one control, shows an instruction, and waits. You do the click or type. Then **Continue** (or F8). STOP cancels the lesson. The live log always records `SKETCH` (rect), `SKETCH skipped`, or `SKETCH failed`. Settings **Test sketch** draws a fixed rectangle for 2 seconds so you can tell the Win32 overlay apart from targeting.

```
observe → plan one human step → sketch → you act → Continue → next step → done
```

## Canvas mode (tldraw / Figma / Paint)

UIA cannot see a whiteboard. A live tldraw tab is ~10 chrome controls and no Draw tool. Canvas mode turns on when the goal matches sketch/draw/paint/tldraw/Figma/canvas, or when the focused window is a browser/whiteboard with a thin tree.

Then Desk Pilot:

1. Biases the model to **screenshots + `drag`**, not more `list_ui` clicks.
2. Attaches a window screenshot after observations on that surface.
3. Lets the model emit several `drag`s in one turn (body rect, cabin, wheel ellipses).
4. Restores the target window if Desk Pilot steals focus after a click.
5. Prefers **create-then-insert**: `prepare_art` (OpenRouter image if the key supports `/images/generations`, else a geometric PNG/SVG). On Windows the PNG is copied to the clipboard (`ctrl+v`). If image gen or clipboard paste is unavailable, the fail is explicit and the geometric drag playbook is the fallback.

## Pull latest (0.2.0-rc)

Canvas drawing: drag tool, thin-tree routing, focus guard, art-goal paste/playbook.

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Then try **auto** (not Guide): `open tldraw and sketch me a car`.

1. A browser should open (or focus) [tldraw.com](https://www.tldraw.com).
2. The log should say **Canvas mode**, then `drag` strokes or `prepare_art` + `ctrl+v` — not a dozen chrome clicks.
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
- Oversized red **STOP** button; Esc requests stop after the current tool/LLM call.
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
```
