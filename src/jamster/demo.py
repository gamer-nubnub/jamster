"""Hardware-free demo: a scripted player and a scripted LLM.

``python -m jamster --demo`` runs the whole pipeline on fake ports so the
dashboard can be seen (and the LLM loop exercised) without loopMIDI, Ableton,
or a Copilot login. Pass ``--demo --llm copilot`` to keep the fake player
but use the real producer/performer.
"""

from __future__ import annotations

import asyncio
import json
import random

from .agents import SessionConfig
from .llm import FakeBackend
from .testing import FakeInput

PROGRESSION = [  # (chord name, MIDI notes, bars)
    ("Cmaj", [60, 64, 67], 4),
    ("Amin", [57, 60, 64], 4),
    ("Fmaj", [53, 57, 60], 2),
    ("G7", [55, 59, 62, 65], 2),
]


async def demo_player(port: FakeInput, session: SessionConfig) -> None:
    """Loops a I–vi–IV–V progression, getting louder every cycle."""
    bar_seconds = session.beats_per_bar * 60.0 / session.bpm
    cycle = 0
    while True:
        for _name, notes, bars in PROGRESSION:
            for _ in range(bars):
                hits = 2 + min(6, cycle * 2)  # energy climbs cycle by cycle
                for i in range(hits):
                    port.play_chord(notes, velocity=min(127, 60 + 10 * cycle + random.randint(-5, 5)))
                    await asyncio.sleep(bar_seconds / hits * 0.5)
                    port.release_all(notes)
                    await asyncio.sleep(bar_seconds / hits * 0.5)
        cycle = (cycle + 1) % 4


def demo_backend() -> FakeBackend:
    """A plausible producer/performer that reacts to chord and energy in the prompt."""

    def responder(role: str, prompt: str) -> str:
        if role == "producer":
            return json.dumps(_plan(prompt))
        return json.dumps(_bar(role, prompt))

    return FakeBackend(responder=responder, delay=1.2)


def _plan(prompt: str) -> dict:
    agents = [a.strip() for a in prompt.rsplit("Use agent names exactly:", 1)[1].rstrip(".").split(",")]
    def parts(intent: str, density: float, energy: float) -> dict:
        return {a: {"intent": f"{intent} ({a})", "density": density, "energy": energy} for a in agents}
    return {
        "score_id": f"s-{random.randint(100, 999)}",
        "ttl_bars": 8,
        "current": "verse",
        "say": {agents[0]: random.choice(["Staying mellow.", "Nice change.", "Want a bridge?", ""])} if agents else {},
        "sections": {
            "verse": {"chords": ["Cmaj", "Amin"], "agents": parts("hold it down", 0.4, 0.4),
                      "transitions": [{"to": "lift", "when": {"energy_above": 0.6, "for_bars": 2}},
                                      {"to": "turnaround", "when": {"chord": "F"}}]},
            "turnaround": {"chords": ["Fmaj", "G7"], "agents": parts("walk up to the top", 0.6, 0.5),
                           "transitions": [{"to": "verse", "when": {"chord": "C"}}]},
            "lift": {"chords": ["Cmaj", "Amin", "Fmaj", "G7"], "agents": parts("open up, drive", 0.8, 0.8),
                     "transitions": [{"to": "verse", "when": {"energy_below": 0.4, "for_bars": 2}}]},
        },
    }


def _bar(role: str, prompt: str) -> dict:
    root = int(prompt.split("root MIDI ")[1].split(")")[0])
    density = float(prompt.split("Density ")[1].split(",")[0])
    if role.endswith("drums"):
        notes = [{"beat": 0, "note": 36, "velocity": 110, "duration": 0.1}, {"beat": 2, "note": 36, "velocity": 100, "duration": 0.1},
                 {"beat": 1, "note": 38, "velocity": 105, "duration": 0.1}, {"beat": 3, "note": 38, "velocity": 105, "duration": 0.1}]
        step = 0.5 if density > 0.5 else 1.0
        hat = 46 if density > 0.7 else 42
        notes += [{"beat": b * step, "note": hat, "velocity": 70, "duration": 0.1} for b in range(int(4 / step))]
        return {"notes": notes}
    notes = [{"beat": 0, "note": root, "velocity": 100, "duration": 1.5}, {"beat": 2, "note": root + 7, "velocity": 90, "duration": 1}]
    if density > 0.5:
        notes += [{"beat": 1.5, "note": root + 12, "velocity": 80, "duration": 0.5}, {"beat": 3, "note": root + 5, "velocity": 85, "duration": 0.5},
                  {"beat": 3.5, "note": root + 7, "velocity": 85, "duration": 0.5}]
    return {"notes": notes, "cc": [{"beat": 0, "controller": 11, "value": int(60 + 60 * density)}]}
