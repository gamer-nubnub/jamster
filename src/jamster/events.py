"""Shared event types for scheduled MIDI output.

These are the units Tier 1 generators (deterministic groove engine now, language
models in Phase 2) emit for each bar. Beat offsets are relative to bar start.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NoteEvent:
    """A note scheduled at a beat offset within a bar."""

    beat: float
    note: int
    velocity: int
    duration_beats: float
    channel: int = 0


@dataclass(frozen=True)
class ControlEvent:
    """A CC value at a beat offset within a bar (expression layer)."""

    beat: float
    controller: int
    value: int
    channel: int = 0


BarEvents = list[NoteEvent | ControlEvent]
