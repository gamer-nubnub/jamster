"""Tier 1 performer: turns a section part into concrete bar notes per agent.

Design (README "Anticipation via branching scores"): the LLM is never on the
bar-boundary hot path. Bars are generated asynchronously per
``(agent, section, chord)`` and cached; a bar boundary takes the cached bar
if present, otherwise the deterministic groove engine, and kicks off
generation for next time. Anticipated sections (transition targets) are
pre-generated too, so by the time the player triggers a change the bar is
usually already there.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .agents import AgentConfig
from .events import BarEvents, ControlEvent, NoteEvent
from .groove import DRUM_CHANNEL, bar_for_role
from .listen import Chord
from .llm import LLMBackend
from .score import Part, Section, chord_matches

SYSTEM_PROMPT = """You are a session musician in a live band, playing one instrument.
You receive a one-bar brief and reply with the exact notes for ONE bar as JSON
only: {"notes":[{"beat":0.0,"note":48,"velocity":100,"duration":1.0}, ...],
"cc":[{"beat":0.0,"controller":11,"value":90}]}.
- beat is the offset from the bar start in beats (0 <= beat < beats_per_bar),
  duration in beats, note is MIDI 0-127, velocity 1-127.
- Stay in the brief's register and role. Lock to the groove. Be tasteful:
  density 0 is almost nothing, 1 is busy.
- Drums use General MIDI percussion numbers (36 kick, 38 snare, 42 closed hat,
  46 open hat, 49 crash, 51 ride). Bass plays MIDI 36-60.
- No prose, no code fences.
"""

BAR_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["notes"],
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["beat", "note", "velocity", "duration"],
                "properties": {
                    "beat": {"type": "number"},
                    "note": {"type": "integer"},
                    "velocity": {"type": "integer"},
                    "duration": {"type": "number"},
                },
            },
        },
        "cc": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["beat", "controller", "value"],
                "properties": {
                    "beat": {"type": "number"},
                    "controller": {"type": "integer"},
                    "value": {"type": "integer"},
                },
            },
        },
    },
}

BarKey = tuple[str, str, str]  # (agent name, section name, chord name)


@dataclass
class Performer:
    backend: LLMBackend | None
    model: str
    beats_per_bar: int = 4
    timeout: float = 20.0
    max_inflight: int = 4
    cache: dict[BarKey, BarEvents] = field(default_factory=dict)
    last: dict[str, BarEvents] = field(default_factory=dict)
    _tasks: dict[BarKey, asyncio.Task[None]] = field(default_factory=dict)
    served_from_model: int = 0
    served_from_fallback: int = 0
    failures: list[str] = field(default_factory=list)

    def events_for(self, agent: AgentConfig, section: Section | None, chord: Chord, bar_index: int, hold: bool) -> BarEvents:
        """Bar for this agent now. Never blocks; never returns nothing."""
        if hold and agent.name in self.last:
            return self.last[agent.name]
        part = section.agents.get(agent.name) if section else None
        density = part.density if part else agent.density
        if section is not None and part is not None and self.backend is not None:
            cached = self._lookup(agent.name, section.name, chord.name)
            if cached is not None:
                self.served_from_model += 1
                self.last[agent.name] = cached
                return cached
            self._ensure((agent.name, section.name, chord.name), agent, part, chord)
        self.served_from_fallback += 1
        events = bar_for_role(agent.role, chord, bar_index, density, agent.channel)
        self.last[agent.name] = events
        return events

    def prefetch(self, agents: list[AgentConfig], anticipated: list[tuple[Section, list[str]]], chord: Chord) -> None:
        """Warm the cache for the current and anticipated sections, each for the
        chords the score expects there (or the current chord if it says nothing)."""
        if self.backend is None:
            return
        for section, chord_names in anticipated:
            chords = [chord]
            for name in chord_names:
                try:
                    chords.append(Chord.parse(name))
                except ValueError:
                    continue
            for agent in agents:
                part = section.agents.get(agent.name)
                if part is None:
                    continue
                for c in chords:
                    if self._lookup(agent.name, section.name, c.name) is None:
                        self._ensure((agent.name, section.name, c.name), agent, part, c)

    def _lookup(self, agent: str, section: str, chord_name: str) -> BarEvents | None:
        """Exact (agent, section, chord) hit, else a bar generated for a chord
        pattern this chord satisfies (``Amin`` bar serves ``Amin7``)."""
        exact = self.cache.get((agent, section, chord_name))
        if exact is not None:
            return exact
        for (a, s, pattern), events in self.cache.items():
            if a == agent and s == section and chord_matches(pattern, chord_name):
                return events
        return None

    @property
    def inflight(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    async def drain(self) -> None:
        pending = [t for t in self._tasks.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _ensure(self, key: BarKey, agent: AgentConfig, part: Part, chord: Chord) -> None:
        if key in self.cache or key in self._tasks and not self._tasks[key].done():
            return
        if self.inflight >= self.max_inflight:
            return
        self._tasks[key] = asyncio.create_task(self._generate(key, agent, part, chord))

    async def _generate(self, key: BarKey, agent: AgentConfig, part: Part, chord: Chord) -> None:
        assert self.backend is not None
        prompt = (
            f"You are {agent.name}, role: {agent.role}. Personality: {agent.personality or 'none'}\n"
            f"Section '{key[1]}'. Brief: {part.intent}\n"
            f"Density {part.density:.2f}, energy {part.energy:.2f}. Chord: {chord.name} "
            f"(root MIDI {chord.root_midi(octave=2)}). {self.beats_per_bar} beats per bar.\n"
            f"Return one bar as JSON."
        )
        try:
            completion = await self.backend.complete(
                role=f"performer:{agent.role}",
                system=SYSTEM_PROMPT,
                prompt=prompt,
                model=self.model,
                schema=BAR_JSON_SCHEMA,
                timeout=self.timeout,
            )
            events = parse_bar(completion.json(), agent, self.beats_per_bar)
            if not events:
                raise ValueError("model returned an empty bar")
            self.cache[key] = events
        except Exception as error:  # noqa: BLE001 - keep the band playing
            self.failures.append(f"{key}: {error!r}")


def parse_bar(data: dict[str, Any], agent: AgentConfig, beats_per_bar: int) -> BarEvents:
    """Validate model output into events; silently drops out-of-range items."""
    channel = DRUM_CHANNEL if agent.role == "drums" else agent.channel
    events: BarEvents = []
    for raw in data.get("notes", []) or []:
        try:
            beat, note, velocity, duration = (
                float(raw["beat"]), int(raw["note"]), int(raw["velocity"]), float(raw["duration"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= beat < beats_per_bar and 0 <= note <= 127 and duration > 0):
            continue
        velocity = min(127, max(1, velocity))
        duration = min(duration, beats_per_bar - beat + 0.5)
        events.append(NoteEvent(beat=beat, note=note, velocity=velocity, duration_beats=duration, channel=channel))
    for raw in data.get("cc", []) or []:
        try:
            beat, controller, value = float(raw["beat"]), int(raw["controller"]), int(raw["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= beat < beats_per_bar and 0 <= controller <= 127:
            events.append(ControlEvent(beat=beat, controller=controller, value=min(127, max(0, value)), channel=channel))
    return events
