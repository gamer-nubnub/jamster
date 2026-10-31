"""Unit coverage for the Phase 1 building blocks."""

import mido

from jamster.clock import BarInfo
from jamster.groove import CLOSED_HAT, DRUM_CHANNEL, KICK, SNARE, bass_bar, drum_bar
from jamster.listen import Chord, ChordTracker, estimate_chord
from jamster.scheduler import _timeline
from jamster.testing import FakeInput


def test_estimate_chord_major_and_minor():
    assert estimate_chord({0, 4, 7}).name == "Cmaj"
    assert estimate_chord({9, 0, 4}).name == "Amin"
    assert estimate_chord({7, 11, 2, 5}).name == "G7"


def test_estimate_chord_needs_two_pitch_classes():
    assert estimate_chord({0}) is None
    assert estimate_chord(set()) is None


def test_chord_tracker_follows_held_notes():
    port = FakeInput()
    tracker = ChordTracker(port)
    assert tracker.current_chord().name == "Cmaj"  # default before any input

    port.play_chord([57, 60, 64])  # A3 C4 E4
    assert tracker.current_chord().name == "Amin"

    port.release_all([57, 60, 64])
    assert tracker.current_chord().name == "Amin"  # recent-note window keeps it


def test_bass_bar_uses_chord_root_and_density():
    sparse = bass_bar(Chord(9, "min"), density=0.1)
    busy = bass_bar(Chord(9, "min"), density=0.9)
    assert sparse[0].note == 57  # root_midi(octave=2): 48 + pitch class
    assert all(e.note % 12 in {9, 4} for e in busy)  # roots and fifths only
    assert len(busy) > len(sparse)


def test_drum_bar_backbeat():
    events = drum_bar(bar_index=0, density=0.4)
    assert all(e.channel == DRUM_CHANNEL for e in events)
    assert {e.beat for e in events if e.note == KICK} == {0.0, 2.0}
    assert {e.beat for e in events if e.note == SNARE} == {1.0, 3.0}
    assert len([e for e in events if e.note == CLOSED_HAT]) == 8


def test_timeline_orders_messages_and_pairs_note_offs():
    bar = BarInfo(index=0, start_time=100.0, seconds_per_beat=0.5)
    messages = _timeline(bar, bass_bar(Chord(0, "maj"), density=0.0))
    times = [t for t, _ in messages]
    assert times == sorted(times)
    assert messages[0] == (100.0, mido.Message("note_on", note=48, velocity=100, channel=0))
    ons = [m for _, m in messages if m.type == "note_on"]
    offs = [m for _, m in messages if m.type == "note_off"]
    assert len(ons) == len(offs)


def test_chord_parse():
    assert Chord.parse("Amin7") == Chord(9, "min7")
    assert Chord.parse("Bb") == Chord(10, "maj")
    assert Chord.parse("F#m") == Chord(6, "min")
    assert Chord.parse("G7") == Chord(7, "7")
    assert Chord.parse("Cmaj7").name == "Cmaj7"
    assert Chord.parse("Dsus4").name == "Dmaj"  # unknown quality → nearest template
