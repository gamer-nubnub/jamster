"""Conductor: the main loop tying clock, listener, score, generators, scheduler.

Per bar boundary (hot path, no LLM):
  1. read chord + energy from the listener,
  2. advance the branching score (transitions, staleness → holding),
  3. pick up a fresh score from the producer if one landed, maybe request one,
  4. per agent: cached model bar, else groove engine; hold-and-loop when holding,
  5. schedule the bar and pre-generate bars for anticipated sections.

Without an LLM backend the band is the deterministic Phase 1 groove engine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

import mido

from .agents import SessionConfig
from .clock import BeatClock, InternalClock, LinkClock, enable_high_resolution_timer
from .listen import ChordTracker
from .llm import LLMBackend
from .midi_io import list_input_ports, open_input, open_output
from .performer import Performer
from .producer import Producer, ProducerContext
from .scheduler import schedule_bar
from .score import ScoreState

OutputFactory = Callable[[str], mido.ports.BaseOutput]
InputFactory = Callable[[str], mido.ports.BaseInput]
ClockFactory = Callable[[float, int], BeatClock]


def _default_input(fragment: str) -> mido.ports.BaseInput:
    return open_input(fragment or (list_input_ports() or [""])[0])


@dataclass
class BarRecord:
    index: int
    chord: str
    energy: float
    section: str | None
    holding_reason: str | None
    score_id: str | None
    sources: dict[str, str] = field(default_factory=dict)  # agent → "model" | "groove" | "loop"


class Conductor:
    """Ties clock, listener, generators, and scheduler together.

    The factories exist so tests (and future UIs) can substitute fake MIDI
    ports, a fast clock, and a scripted LLM without touching the bar loop.
    """

    def __init__(
        self,
        session: SessionConfig,
        use_link: bool = False,
        backend: LLMBackend | None = None,
        open_output: OutputFactory = open_output,
        open_input: InputFactory = _default_input,
        make_clock: ClockFactory | None = None,
        producer_model: str | None = None,
        performer_model: str | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.session = session
        self.use_link = use_link
        self.backend = backend
        self._open_output = open_output
        self._open_input = open_input
        self._make_clock_override = make_clock
        self._log = log
        self._ports: dict[str, mido.ports.BaseOutput] = {}
        self.transcript: list[str] = []
        self.messages: list[tuple[int, str, str]] = []  # (bar, agent, text) shown in the UI
        self.state = ScoreState()
        self.performer = Performer(
            backend, performer_model or session.models.performer, beats_per_bar=session.beats_per_bar
        )
        self.producer = (
            Producer(backend, session, producer_model or session.models.producer, every_bars=session.models.producer_every_bars)
            if backend is not None
            else None
        )
        self.bars: list[BarRecord] = []

    @property
    def bar_log(self) -> list[tuple[int, str]]:
        return [(b.index, b.chord) for b in self.bars]

    def _open_ports(self) -> None:
        for agent in self.session.agents:
            self._ports[agent.name] = self._open_output(agent.port)

    def _open_listener(self) -> ChordTracker:
        return ChordTracker(self._open_input(self.session.input_port))

    def _make_clock(self) -> BeatClock:
        if self._make_clock_override is not None:
            return self._make_clock_override(self.session.bpm, self.session.beats_per_bar)
        if self.use_link:
            return LinkClock(self.session.bpm, self.session.beats_per_bar)
        return InternalClock(self.session.bpm, self.session.beats_per_bar)

    def request_plan(self) -> None:
        """Voice/UI hook: ask the producer for a new plan at the next bar."""
        self._force_plan = True

    _force_plan = False

    def _tick_producer(self, bar_index: int, chord: str, energy: float) -> None:
        if self.producer is None:
            return
        fresh = self.producer.take()
        if fresh is not None:
            self.state.accept(fresh, bar_index)
            for agent, text in fresh.say.items():
                last = next((m[2] for m in reversed(self.messages) if m[1] == agent), None)
                if text != last:  # a re-plan repeating itself is not news
                    self.messages.append((bar_index, agent, text))
            self._log(f"[plan] {fresh.score_id}: {' | '.join(fresh.sections)} → current '{fresh.current}'")
        context = ProducerContext(
            bar_index=bar_index,
            chord=chord,
            energy=energy,
            history=list(self.state.history),
            transcript=list(self.transcript),
            active_section=self.state.section_name,
            previous_score=self.state.score,
        )
        if self.producer.maybe_request(context, force=self._force_plan):
            self._force_plan = False

    async def run(self, bars: int | None = None) -> None:
        enable_high_resolution_timer()
        self._open_ports()
        tracker = self._open_listener()
        clock = self._make_clock()
        if self.backend is not None:
            await self.backend.start()
        self._log(
            f"[jamster] session '{self.session.name}' @ {self.session.bpm:.0f} bpm, "
            f"agents: {', '.join(agent.name for agent in self.session.agents)}, "
            f"llm: {'on' if self.backend else 'off'}"
        )
        try:
            while True:
                bar = await clock.wait_for_bar_start()
                if bars is not None and bar.index >= bars:
                    break
                chord, energy = tracker.current_chord(), tracker.energy()
                section = self.state.advance(bar.index, chord.name, energy)
                self._tick_producer(bar.index, chord.name, energy)
                section = self.state.section  # a fresh plan may have changed it
                record = BarRecord(
                    bar.index, chord.name, energy, section.name if section else None,
                    self.state.holding_reason, self.state.score.score_id if self.state.score else None,
                )
                for agent in self.session.agents:
                    before = (self.performer.served_from_model, self.performer.served_from_fallback)
                    events = self.performer.events_for(agent, section, chord, bar.index, hold=self.state.loop)
                    after = (self.performer.served_from_model, self.performer.served_from_fallback)
                    record.sources[agent.name] = "model" if after[0] > before[0] else "groove" if after[1] > before[1] else "loop"
                    schedule_bar(self._ports[agent.name], bar, events)
                self.performer.prefetch(self.session.agents, self.state.anticipated(), chord)
                self.bars.append(record)
                status = f"HOLD ({self.state.holding_reason})" if self.state.loop else f"section {record.section}"
                self._log(f"[bar {bar.index + 1:>3}] {chord.name:<6} e={energy:.2f} {status} {record.sources}")
        finally:
            await asyncio.sleep(clock.beats_per_bar * 60.0 / clock.bpm)  # let note-offs drain
            for port in self._ports.values():
                port.close()
            if self.backend is not None:
                if self.producer is not None:
                    await self.producer.cancel()
                await self.performer.drain()
                await self.backend.stop()
