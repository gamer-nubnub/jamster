"""Fakes for running the full pipeline without loopMIDI or Ableton.

``FakeOutput`` records every sent message with a perf_counter timestamp,
``FakeInput`` lets a test play notes into the ChordTracker, and ``fast_clock``
builds an InternalClock at a very high tempo so an 8-bar jam runs in well
under a second while still exercising real scheduling and sleep paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import mido

from .clock import BeatClock, InternalClock


@dataclass
class SentMessage:
    at: float  # perf_counter when sent
    message: mido.Message


@dataclass
class FakeOutput:
    name: str = "fake-out"
    sent: list[SentMessage] = field(default_factory=list)
    closed: bool = False

    def send(self, message: mido.Message) -> None:
        self.sent.append(SentMessage(time.perf_counter(), message))

    def close(self) -> None:
        self.closed = True

    def note_ons(self) -> list[mido.Message]:
        return [s.message for s in self.sent if s.message.type == "note_on" and s.message.velocity > 0]


@dataclass
class FakeInput:
    name: str = "fake-in"
    callback: Callable[[mido.Message], None] | None = None

    def feed(self, message: mido.Message) -> None:
        if self.callback is not None:
            self.callback(message)

    def play_chord(self, notes: list[int], velocity: int = 90) -> None:
        for note in notes:
            self.feed(mido.Message("note_on", note=note, velocity=velocity))

    def release_all(self, notes: list[int]) -> None:
        for note in notes:
            self.feed(mido.Message("note_off", note=note, velocity=0))

    def close(self) -> None:
        pass


class FakePorts:
    """Factories for the Conductor plus handles to every port it opened."""

    def __init__(self) -> None:
        self.outputs: dict[str, FakeOutput] = {}
        self.input = FakeInput()

    def open_output(self, fragment: str) -> FakeOutput:
        port = FakeOutput(name=fragment)
        self.outputs[fragment] = port
        return port

    def open_input(self, fragment: str) -> FakeInput:
        return self.input


def fast_clock(bpm: float = 3000.0) -> Callable[[float, int], BeatClock]:
    """Clock factory ignoring the session tempo in favour of a fast one."""

    def make(_session_bpm: float, beats_per_bar: int) -> BeatClock:
        return InternalClock(bpm, beats_per_bar)

    return make
