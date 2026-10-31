"""Live-MIDI listening: track held notes and estimate the current chord.

Phase 1 scope: symbolic chord estimation from the user's MIDI instrument via
pitch-class template matching. Leader-track audio analysis and AbletonOSC
project ingest arrive in Phase 4 and will feed the same Chord type.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import mido

PITCH_CLASS_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = {"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10, "Cb": 11, "Fb": 4}

# Interval templates, checked in order; first structures win ties.
CHORD_TEMPLATES: dict[str, frozenset[int]] = {
    "maj": frozenset({0, 4, 7}),
    "min": frozenset({0, 3, 7}),
    "7": frozenset({0, 4, 7, 10}),
    "min7": frozenset({0, 3, 7, 10}),
    "maj7": frozenset({0, 4, 7, 11}),
}

RECENT_NOTE_WINDOW_SECONDS = 2.0
ENERGY_WINDOW_SECONDS = 2.0
ENERGY_FULL_SCALE = 8 * 100.0  # eight forte notes in the window ≈ energy 1.0


@dataclass(frozen=True)
class Chord:
    root_pitch_class: int
    quality: str

    @property
    def name(self) -> str:
        return f"{PITCH_CLASS_NAMES[self.root_pitch_class]}{self.quality}"

    def root_midi(self, octave: int = 2) -> int:
        """MIDI note of the root; octave 2 puts C at 48 (bass register)."""
        return 12 * (octave + 2) + self.root_pitch_class

    @staticmethod
    def parse(name: str) -> "Chord":
        """``"Amin7"`` → Chord(9, "min7"); unknown qualities fall back to maj/min."""
        text = name.strip()
        if len(text) >= 2 and text[:2] in FLAT_NAMES:
            root, quality = FLAT_NAMES[text[:2]], text[2:]
        elif len(text) >= 2 and text[:2] in PITCH_CLASS_NAMES:
            root, quality = PITCH_CLASS_NAMES.index(text[:2]), text[2:]
        elif text[:1].upper() in PITCH_CLASS_NAMES:
            root, quality = PITCH_CLASS_NAMES.index(text[:1].upper()), text[1:]
        else:
            raise ValueError(f"not a chord name: {name!r}")
        quality = quality.strip().lower()
        if quality in CHORD_TEMPLATES:
            return Chord(root, quality)
        if quality in ("", "major", "M"):
            return Chord(root, "maj")
        if quality.startswith(("m", "min", "-")) and not quality.startswith("maj"):
            return Chord(root, "min7" if "7" in quality else "min")
        return Chord(root, "7" if "7" in quality else "maj")


DEFAULT_CHORD = Chord(root_pitch_class=0, quality="maj")


def estimate_chord(pitch_classes: set[int]) -> Chord | None:
    """Best template match over all roots; None if fewer than two pitch classes."""
    if len(pitch_classes) < 2:
        return None
    best: tuple[float, Chord] | None = None
    for root in range(12):
        relative = {(pc - root) % 12 for pc in pitch_classes}
        for quality, template in CHORD_TEMPLATES.items():
            overlap = len(relative & template)
            extras = len(relative - template)
            missing = len(template - relative)
            score = overlap - 0.5 * extras - 0.3 * missing
            if 0 not in relative:  # chords without their root are unlikely
                score -= 1.0
            if best is None or score > best[0]:
                best = (score, Chord(root, quality))
    return best[1] if best else None


class ChordTracker:
    """Consumes a MIDI input stream and exposes the current chord estimate.

    Held notes take priority; recently released notes (within
    RECENT_NOTE_WINDOW_SECONDS) fill in when the player lifts between bars.
    """

    def __init__(self, port: mido.ports.BaseInput) -> None:
        self._held: set[int] = set()
        self._recent: dict[int, float] = {}
        self._last_chord: Chord = DEFAULT_CHORD
        self._onsets: list[tuple[float, int]] = []  # (time, velocity)
        port.callback = self._on_message

    def _on_message(self, message: mido.Message) -> None:
        if message.type == "note_on" and message.velocity > 0:
            self._held.add(message.note)
            self._recent[message.note] = time.monotonic()
            self._onsets.append((time.monotonic(), message.velocity))
        elif message.type in ("note_off", "note_on"):
            self._held.discard(message.note)
            self._recent[message.note] = time.monotonic()

    def energy(self) -> float:
        """0-1 playing intensity: velocity-weighted onsets in the recent window."""
        now = time.monotonic()
        self._onsets = [(at, vel) for at, vel in self._onsets if now - at < ENERGY_WINDOW_SECONDS]
        return min(1.0, sum(vel for _, vel in self._onsets) / ENERGY_FULL_SCALE)

    def current_chord(self) -> Chord:
        now = time.monotonic()
        notes = set(self._held) | {
            note for note, at in self._recent.items() if now - at < RECENT_NOTE_WINDOW_SECONDS
        }
        estimate = estimate_chord({note % 12 for note in notes})
        if estimate is not None:
            self._last_chord = estimate
        return self._last_chord
