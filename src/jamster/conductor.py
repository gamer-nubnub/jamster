"""Conductor: the main loop tying clock, listener, generators, and scheduler.

Phase 1 behaviour: at each bar boundary, read the current chord estimate and
schedule this bar's events per agent from the deterministic groove engine
(generation cost is microseconds, so zero lookahead is fine). Phase 2 moves
generation to the midpoint of the previous bar to give the Tier 1 model a
half-bar deadline, with this engine as the fallback.
"""

from __future__ import annotations

import asyncio

import mido

from .agents import AgentConfig, SessionConfig
from .clock import BeatClock, InternalClock, LinkClock, enable_high_resolution_timer
from .groove import bar_for_role
from .listen import ChordTracker
from .midi_io import list_input_ports, open_input, open_output
from .scheduler import schedule_bar


class Conductor:
    def __init__(self, session: SessionConfig, use_link: bool = False) -> None:
        self.session = session
        self.use_link = use_link
        self._ports: dict[str, mido.ports.BaseOutput] = {}

    def _open_ports(self) -> None:
        for agent in self.session.agents:
            self._ports[agent.name] = open_output(agent.port)

    def _open_listener(self) -> ChordTracker:
        fragment = self.session.input_port or (list_input_ports() or [""])[0]
        return ChordTracker(open_input(fragment))

    def _make_clock(self) -> BeatClock:
        if self.use_link:
            return LinkClock(self.session.bpm, self.session.beats_per_bar)
        return InternalClock(self.session.bpm, self.session.beats_per_bar)

    def _agent_events(self, agent: AgentConfig, chord, bar_index: int):
        return bar_for_role(agent.role, chord, bar_index, agent.density, agent.channel)

    async def run(self, bars: int | None = None) -> None:
        enable_high_resolution_timer()
        self._open_ports()
        tracker = self._open_listener()
        clock = self._make_clock()
        print(f"[jamster] session '{self.session.name}' @ {self.session.bpm:.0f} bpm, "
              f"agents: {', '.join(agent.name for agent in self.session.agents)}")
        try:
            while True:
                bar = await clock.wait_for_bar_start()
                if bars is not None and bar.index >= bars:
                    break
                chord = tracker.current_chord()
                print(f"[bar {bar.index + 1:>3}] chord {chord.name}")
                for agent in self.session.agents:
                    events = self._agent_events(agent, chord, bar.index)
                    schedule_bar(self._ports[agent.name], bar, events)
        finally:
            await asyncio.sleep(clock.beats_per_bar * 60.0 / clock.bpm)  # let note-offs drain
            for port in self._ports.values():
                port.close()
