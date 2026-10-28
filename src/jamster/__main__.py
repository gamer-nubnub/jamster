"""CLI entry: python -m jamster --session config/sessions/demo.yaml"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .agents import load_session
from .conductor import Conductor
from .midi_io import list_input_ports, list_output_ports


def main() -> None:
    parser = argparse.ArgumentParser(prog="jamster", description="Agentic band-practice conductor")
    parser.add_argument("--session", type=Path, default=Path("config/sessions/demo.yaml"))
    parser.add_argument("--bpm", type=float, help="override session tempo")
    parser.add_argument("--bars", type=int, help="stop after N bars (default: run until Ctrl+C)")
    parser.add_argument("--link", action="store_true", help="sync to Ableton Link instead of internal clock")
    parser.add_argument("--list-ports", action="store_true", help="list MIDI ports and exit")
    args = parser.parse_args()

    if args.list_ports:
        print("Outputs:", *list_output_ports(), sep="\n  ")
        print("Inputs:", *list_input_ports(), sep="\n  ")
        return

    session = load_session(args.session)
    if args.bpm:
        session = type(session)(**{**session.__dict__, "bpm": args.bpm})
    try:
        asyncio.run(Conductor(session, use_link=args.link).run(bars=args.bars))
    except KeyboardInterrupt:
        print("\n[jamster] stopped")


if __name__ == "__main__":
    main()
