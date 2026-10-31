"""CLI entry: python -m jamster --session config/sessions/demo.yaml"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .agents import load_session
from .conductor import Conductor
from .llm import make_backend
from .midi_io import list_input_ports, list_output_ports


def main() -> None:
    parser = argparse.ArgumentParser(prog="jamster", description="Agentic band-practice conductor")
    parser.add_argument("--session", type=Path, default=Path("config/sessions/demo.yaml"))
    parser.add_argument("--bpm", type=float, help="override session tempo")
    parser.add_argument("--bars", type=int, help="stop after N bars (default: run until Ctrl+C)")
    parser.add_argument("--link", action="store_true", help="sync to Ableton Link instead of internal clock")
    parser.add_argument("--list-ports", action="store_true", help="list MIDI ports and exit")
    parser.add_argument("--llm", choices=["copilot", "none"], default="copilot",
                        help="LLM backend for producer/performer (default: copilot)")
    parser.add_argument("--producer-model", help="override the session's producer model id")
    parser.add_argument("--performer-model", help="override the session's performer model id")
    parser.add_argument("--list-models", action="store_true", help="list LLM model ids and exit")
    args = parser.parse_args()

    if args.list_ports:
        print("Outputs:", *list_output_ports(), sep="\n  ")
        print("Inputs:", *list_input_ports(), sep="\n  ")
        return

    if args.list_models:
        backend = make_backend("copilot")
        assert backend is not None

        async def show() -> None:
            await backend.start()
            try:
                print(*await backend.list_models(), sep="\n")
            finally:
                await backend.stop()

        asyncio.run(show())
        return

    session = load_session(args.session)
    if args.bpm:
        session = type(session)(**{**session.__dict__, "bpm": args.bpm})
    backend = make_backend(args.llm)
    conductor = Conductor(
        session,
        use_link=args.link,
        backend=backend,
        producer_model=args.producer_model,
        performer_model=args.performer_model,
    )
    try:
        asyncio.run(conductor.run(bars=args.bars))
    except KeyboardInterrupt:
        print("\n[jamster] stopped")


if __name__ == "__main__":
    main()
