"""Phase 0 timing probe: go/no-go evidence for the MIDI scheduling path.

Schedules a metronome (woodblock accents) through the real clock + scheduler
into a MIDI output port and measures scheduled-vs-actual send time for every
message. Pass criterion: p95 jitter < 10 ms.

Usage (from repo root, .venv active):
    python scripts/probe_timing.py --port jamster --bpm 120 --bars 8
    python scripts/probe_timing.py --link        # sync to Ableton Link instead

Requires at least one loopMIDI output port (any name; match with --port).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jamster.clock import BarInfo, InternalClock, LinkClock, enable_high_resolution_timer
from jamster.events import NoteEvent
from jamster.midi_io import list_output_ports, open_output
from jamster.scheduler import play_bar

HIGH_WOODBLOCK = 76
LOW_WOODBLOCK = 77
PASS_THRESHOLD_MS = 10.0


def metronome_bar(beats_per_bar: int) -> list[NoteEvent]:
    return [
        NoteEvent(beat=float(beat), note=HIGH_WOODBLOCK if beat == 0 else LOW_WOODBLOCK,
                  velocity=110 if beat == 0 else 85, duration_beats=0.1)
        for beat in range(beats_per_bar)
    ]


def report(deltas_ms: list[float]) -> bool:
    mean = statistics.fmean(deltas_ms)
    p95 = statistics.quantiles(deltas_ms, n=20)[18] if len(deltas_ms) >= 20 else max(deltas_ms)
    worst = max(deltas_ms)
    print(f"\n[3] Results over {len(deltas_ms)} messages:")
    print(f"    mean jitter  {mean:7.3f} ms")
    print(f"    p95 jitter   {p95:7.3f} ms")
    print(f"    worst jitter {worst:7.3f} ms")
    buckets = [0.5, 1, 2, 5, 10, 50]
    print("    histogram (ms):")
    lower = 0.0
    for upper in buckets:
        count = sum(1 for d in deltas_ms if lower <= d < upper)
        print(f"      {lower:>5.1f}-{upper:<5.1f} {'#' * count} ({count})")
        lower = upper
    over = sum(1 for d in deltas_ms if d >= buckets[-1])
    print(f"      >{buckets[-1]:<9.1f} {'#' * over} ({over})")
    return p95 < PASS_THRESHOLD_MS


async def run_probe(port_fragment: str, bpm: float, bars: int, use_link: bool) -> bool:
    print(f"[1] Opening MIDI output matching '{port_fragment}'...")
    try:
        port = open_output(port_fragment)
    except LookupError as error:
        print(f"    FAIL: {error}")
        return False
    print(f"    OK: {port.name}")

    clock = LinkClock(bpm) if use_link else InternalClock(bpm)
    source = "Ableton Link" if use_link else f"internal @ {bpm:.0f} bpm"
    print(f"[2] Running metronome for {bars} bars ({source})...")

    deltas_ms: list[float] = []

    def observe(target: float, actual: float) -> None:
        deltas_ms.append((actual - target) * 1000.0)

    events = metronome_bar(clock.beats_per_bar)
    for _ in range(bars):
        bar: BarInfo = await clock.wait_for_bar_start()
        await play_bar(port, bar, events, on_send=observe)
    port.close()
    return report(deltas_ms)


def main() -> None:
    parser = argparse.ArgumentParser(description="jamster Phase 0 timing probe")
    parser.add_argument("--port", default="jamster", help="output port name fragment")
    parser.add_argument("--bpm", type=float, default=120.0)
    parser.add_argument("--bars", type=int, default=8)
    parser.add_argument("--link", action="store_true", help="use Ableton Link clock")
    parser.add_argument("--list-ports", action="store_true")
    args = parser.parse_args()

    if args.list_ports:
        print("Outputs:", *list_output_ports(), sep="\n  ")
        return

    enable_high_resolution_timer()
    passed = asyncio.run(run_probe(args.port, args.bpm, args.bars, args.link))
    print(f"\n[4] RESULT: {'PASS' if passed else 'FAIL'} (p95 < {PASS_THRESHOLD_MS} ms required)")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
