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
observe (UIA tree) → plan (OpenRouter tools) → act (click / type / hotkey) → verify → repeat
```

Tools the model can call:

| Tool | Purpose |
| --- | --- |
| `list_ui` | Compact UIA tree: name, type, automation id, rect, short path |
| `click` | By automation id, name, or coordinates |
| `type_text` | Type into the focused or targeted control |
| `hotkey` | `win+r`, `enter`, `ctrl+s`, … |
| `list_windows` / `focus_window` | Reuse an already-open app instead of launching another copy |
| `launch_app` | Start an installed app, or focus it if it is already running |
| `screenshot_region` | mss crop; last resort |
| `wait_for_window` | Title / focus change |
| `done` / `fail` | End the run |

Default model: `openai/gpt-6-luna`. Reasoning is requested with `reasoning.effort = low` so steps stay snappy. The loop is plain Python (no LangChain / LangGraph / CrewAI).

Typical “open Notepad” path: if Notepad is already in `top_windows`, `focus_window` (or `launch_app`, which reuses). Otherwise `launch_app notepad` → `wait_for_window` → `type_text hello` → `done`.

## Auto vs Guide

- **Auto** (default): a goal like `Open Helium and search for a dank meme` — the agent clicks and types. No sketch overlay.
- **Guide**: a goal like `How to open Helium and search for a dank meme`, or Settings **Guide mode**. The agent sketches one control, shows an instruction, and waits. You do the click or type. Then **Continue** (or F8). STOP cancels the lesson.

```
observe → plan one human step → sketch → you act → Continue → next step → done
```

## Pull latest (0.1.4)

Sketch is for how-to lessons only — not agent auto-clicks:

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
