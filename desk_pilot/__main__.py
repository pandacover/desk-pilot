from __future__ import annotations

import argparse
import sys

from desk_pilot import APP_NAME, DEFAULT_MAX_STEPS, DEFAULT_MODEL, __version__
from desk_pilot.app.config import load_settings
from desk_pilot.desktop import get_backend, is_windows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desk_pilot",
        description=f"{APP_NAME} — local Windows computer-use agent (UI Automation + OpenRouter).",
    )
    parser.add_argument("--cli", action="store_true", help="Run a goal in the terminal (no window).")
    parser.add_argument("--goal", type=str, help="Goal text, required with --cli.")
    parser.add_argument("--mock", action="store_true", help="Force the dry-run desktop stub.")
    parser.add_argument("--model", type=str, help="OpenRouter model id (default openai/gpt-6-luna).")
    parser.add_argument("--max-steps", type=int, dest="max_steps", help="Hard step budget (default 30).")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    force_mock = bool(args.mock) or not is_windows()
    if args.cli:
        return _run_cli(args, force_mock=force_mock)
    from desk_pilot.app.ui import run_app

    run_app(force_mock=force_mock)
    return 0


def _run_cli(args: argparse.Namespace, *, force_mock: bool) -> int:
    from desk_pilot.agent.loop import AgentLoop
    from desk_pilot.llm.openrouter import OpenRouterClient

    goal = (args.goal or "").strip()
    if not goal:
        print("desk_pilot --cli requires --goal \"...\"", file=sys.stderr)
        return 2
    settings = load_settings()
    key = settings.effective_api_key()
    if not key:
        print(
            "No OpenRouter API key. Paste one in the app Settings, or export OPENROUTER_API_KEY.",
            file=sys.stderr,
        )
        return 2
    backend = get_backend(force_mock=force_mock)
    model = args.model or settings.model or DEFAULT_MODEL
    max_steps = args.max_steps or settings.max_steps or DEFAULT_MAX_STEPS
    client = OpenRouterClient(key, model, reasoning_effort=settings.reasoning_effort or "low")

    def log(kind: str, message: str) -> None:
        print(f"[{kind}] {message}", flush=True)

    from desk_pilot.desktop.com import com_thread

    try:
        with com_thread():
            result = AgentLoop(
                backend=backend,
                llm=client,
                max_steps=max_steps,
                on_log=log,
            ).run(goal)
    finally:
        client.close()
    print(f"\n{result.status.upper()}: {result.message}  (steps={result.steps})")
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
