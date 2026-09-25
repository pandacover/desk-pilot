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
| `click` | By automation id, name, or coordinates. Briefly sketches the target on Windows. |
| `type_text` | Type into the focused or targeted control (same sketch overlay) |
| `hotkey` | `win+r`, `enter`, `ctrl+s`, … |
| `list_windows` / `focus_window` | Reuse an already-open app instead of launching another copy |
| `launch_app` | Start an installed app, or focus it if it is already running |
| `screenshot_region` | mss crop; last resort |
| `wait_for_window` | Title / focus change |
| `done` / `fail` | End the run |

Default model: `openai/gpt-6-luna`. Reasoning is requested with `reasoning.effort = low` so steps stay snappy. The loop is plain Python (no LangChain / LangGraph / CrewAI).

Typical “open Notepad” path: if Notepad is already in `top_windows`, `focus_window` (or `launch_app`, which reuses). Otherwise `launch_app notepad` → `wait_for_window` → `type_text hello` → `done`.

On Windows, each click or type flashes a short **sketch outline** around the UIA bounding rect (wobbly pencil stroke, ~400ms, click-through). Turn it off in Settings if you do not want it. Hotkeys and `launch_app` skip the overlay.

## Pull latest (0.1.3)

Sketch overlay around the control the agent is about to click or type into:

```powershell
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Toggle **Sketch overlay on click/type** in Settings (saved in `config.json`, default on).

## Pull latest (0.1.2)

If a second run opened another Helium window, or a mid-run step died with OpenRouter HTTP 400 `No tool call found for function call output with call_id …`, pull this build:

```bash
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Desk Pilot now lists top-level windows on every observe. `launch_app` focuses an existing instance when it finds one. Tool results stay paired with their assistant `tool_calls` so long runs do not send orphan `call_id`s to OpenRouter.

## Pull latest (0.1.1)

If a live Windows run showed **every OBSERVE as `Window: ? · 0 controls`** and the model mentioned `CoInitialize has not been called`, you are on a build that never initialized COM on the agent worker thread. Pull this repo and reinstall:

```bash
git pull
pip install -r requirements.txt
python -m desk_pilot
```

Desk Pilot now calls `CoInitializeEx` (STA) on **every thread** that uses UI Automation (the CustomTkinter worker and `--cli`), then recreates `uiautomation`’s COM singleton on that thread. Failed `list_ui` calls surface the COM error in the live log instead of an empty tree.

`launch_app` looks up installed programs via PATH, registry App Paths, Start Menu shortcuts, and `shell:AppsFolder`. Prefer it over Win+R for browsers and other apps that are not on PATH. If Win+R shows “Windows cannot find”, dismiss the dialog and try `launch_app` or Start search.

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
