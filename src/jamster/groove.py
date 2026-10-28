"""Deterministic groove engine: the Tier 1 fallback and the whole of Phase 1.

Emits one bar of events per agent role from the current chord and a density
trait. When language-model generation lands in Phase 2, this engine stays as
the deadline-miss fallback so the band never drops a bar.
"""

from __future__ import annotations

from .events import BarEvents, NoteEvent
from .listen import Chord

# General MIDI percussion (channel 10 = mido channel 9)
DRUM_CHANNEL = 9
KICK = 36
SNARE = 38
CLOSED_HAT = 42
OPEN_HAT = 46

FIFTH_INTERVAL = 7
OCTAVE_INTERVAL = 12


def bass_bar(chord: Chord, density: float, channel: int = 0) -> BarEvents:
    """Roots and fifths; eighth-note movement appears as density rises."""
    root = chord.root_midi(octave=2)
    events: list[NoteEvent] = [
        NoteEvent(beat=0.0, note=root, velocity=100, duration_beats=1.5, channel=channel),
        NoteEvent(beat=2.0, note=root + FIFTH_INTERVAL, velocity=90, duration_beats=1.0, channel=channel),
    ]
    if density >= 0.3:
        events.append(NoteEvent(beat=3.0, note=root, velocity=85, duration_beats=0.5, channel=channel))
    if density >= 0.6:
        events.append(NoteEvent(beat=1.5, note=root + OCTAVE_INTERVAL, velocity=80, duration_beats=0.5, channel=channel))
        events.append(NoteEvent(beat=3.5, note=root + FIFTH_INTERVAL, velocity=80, duration_beats=0.5, channel=channel))
    return list(events)


def drum_bar(bar_index: int, density: float) -> BarEvents:
    """Straight pop-rock 4/4: kick 1+3, snare 2+4, eighth hats, bar-4 open hat."""
    events: list[NoteEvent] = [
        NoteEvent(beat=0.0, note=KICK, velocity=110, duration_beats=0.1, channel=DRUM_CHANNEL),
        NoteEvent(beat=2.0, note=KICK, velocity=100, duration_beats=0.1, channel=DRUM_CHANNEL),
        NoteEvent(beat=1.0, note=SNARE, velocity=105, duration_beats=0.1, channel=DRUM_CHANNEL),
        NoteEvent(beat=3.0, note=SNARE, velocity=105, duration_beats=0.1, channel=DRUM_CHANNEL),
    ]
    hat_step = 0.5 if density >= 0.3 else 1.0
    beat = 0.0
    while beat < 4.0:
        accent = 95 if beat % 1.0 == 0 else 70
        events.append(NoteEvent(beat=beat, note=CLOSED_HAT, velocity=accent, duration_beats=0.1, channel=DRUM_CHANNEL))
        beat += hat_step
    if bar_index % 4 == 3:  # small turnaround marker every fourth bar
        events.append(NoteEvent(beat=3.5, note=OPEN_HAT, velocity=90, duration_beats=0.3, channel=DRUM_CHANNEL))
    return list(events)


def bar_for_role(role: str, chord: Chord, bar_index: int, density: float, channel: int) -> BarEvents:
    if role == "bass":
        return bass_bar(chord, density, channel)
    if role == "drums":
        return drum_bar(bar_index, density)
    raise ValueError(f"Unknown agent role: {role!r}")
