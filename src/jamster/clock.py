"""Beat clocks: internal metronome and Ableton Link (optional).

The conductor depends only on the small BeatClock protocol, so Ableton Link can
be swapped in without touching scheduling logic. Link support requires the
optional ``aalink`` dependency (``pip install jamster[link]``) and is validated
end-to-end by ``scripts/probe_timing.py``.

Windows note: the default system timer granularity (~15 ms) would sink the
< 10 ms jitter budget, so ``enable_high_resolution_timer`` must be called once
at process start, and ``sleep_until`` busy-waits the last few milliseconds.
The busy-wait blocks the event loop for at most BUSY_WAIT_WINDOW_SECONDS,
which is acceptable for this prototype's single-conductor design.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time
from dataclasses import dataclass
from typing import Protocol

BUSY_WAIT_WINDOW_SECONDS = 0.003


def enable_high_resolution_timer() -> None:
    """Request 1 ms Windows timer resolution; no-op elsewhere."""
    if sys.platform == "win32":
        ctypes.windll.winmm.timeBeginPeriod(1)


async def sleep_until(target_perf_counter: float) -> None:
    """Async-sleep to just before the target time, then busy-wait the rest."""
    while True:
        remaining = target_perf_counter - time.perf_counter()
        if remaining <= BUSY_WAIT_WINDOW_SECONDS:
            break
        await asyncio.sleep(remaining - BUSY_WAIT_WINDOW_SECONDS)
    while time.perf_counter() < target_perf_counter:
        pass


@dataclass(frozen=True)
class BarInfo:
    """A bar boundary: index since clock start and its perf_counter timestamp."""

    index: int
    start_time: float
    seconds_per_beat: float


class BeatClock(Protocol):
    """Minimal clock contract used by the conductor and scheduler."""

    bpm: float
    beats_per_bar: int

    async def wait_for_bar_start(self) -> BarInfo: ...


class InternalClock:
    """Free-running metronome clock; the default when Ableton Link is absent."""

    def __init__(self, bpm: float, beats_per_bar: int = 4) -> None:
        self.bpm = bpm
        self.beats_per_bar = beats_per_bar
        self._start = time.perf_counter()
        self._next_bar_index = 0

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    async def wait_for_bar_start(self) -> BarInfo:
        bar_index = self._next_bar_index
        self._next_bar_index += 1
        bar_start = self._start + bar_index * self.beats_per_bar * self.seconds_per_beat
        await sleep_until(bar_start)
        return BarInfo(bar_index, bar_start, self.seconds_per_beat)


class LinkClock:
    """Ableton Link clock via aalink; keeps bars phase-locked to Live.

    Tempo is read back from the Link session each bar, so tempo changes in
    Ableton propagate within one bar.
    """

    def __init__(self, bpm: float, beats_per_bar: int = 4) -> None:
        try:
            from aalink import Link
        except ImportError as error:  # pragma: no cover - depends on extras
            raise RuntimeError(
                "Ableton Link requested but aalink is not installed; "
                "run: pip install jamster[link]"
            ) from error
        self.bpm = bpm
        self.beats_per_bar = beats_per_bar
        self._link = Link(bpm, asyncio.get_event_loop())
        self._bar_index = 0

    async def wait_for_bar_start(self) -> BarInfo:
        await self._link.sync(self.beats_per_bar)
        self.bpm = float(self._link.tempo)
        info = BarInfo(self._bar_index, time.perf_counter(), 60.0 / self.bpm)
        self._bar_index += 1
        return info
