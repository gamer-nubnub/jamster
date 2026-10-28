"""Lookahead scheduler: plays one bar of events at precise wall-clock offsets.

Bar events (notes + CC) are flattened into a timeline of raw MIDI messages,
then a task walks the timeline with ``sleep_until``. One task per agent per
bar keeps agents independent: a slow generator for one agent cannot delay
another agent's playback.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import mido

from .clock import BarInfo, sleep_until
from .events import BarEvents, ControlEvent, NoteEvent

# Optional (target_time, actual_time) hook, used by the timing probe.
SendObserver = Callable[[float, float], None]


def _timeline(bar: BarInfo, events: BarEvents) -> list[tuple[float, mido.Message]]:
    messages: list[tuple[float, mido.Message]] = []
    for event in events:
        at = bar.start_time + event.beat * bar.seconds_per_beat
        if isinstance(event, NoteEvent):
            messages.append(
                (at, mido.Message("note_on", note=event.note, velocity=event.velocity, channel=event.channel))
            )
            off_at = at + event.duration_beats * bar.seconds_per_beat
            messages.append(
                (off_at, mido.Message("note_off", note=event.note, velocity=0, channel=event.channel))
            )
        elif isinstance(event, ControlEvent):
            messages.append(
                (at, mido.Message("control_change", control=event.controller, value=event.value, channel=event.channel))
            )
    messages.sort(key=lambda pair: pair[0])
    return messages


async def play_bar(
    port: mido.ports.BaseOutput,
    bar: BarInfo,
    events: BarEvents,
    on_send: SendObserver | None = None,
) -> None:
    import time

    for target_time, message in _timeline(bar, events):
        await sleep_until(target_time)
        port.send(message)
        if on_send is not None:
            on_send(target_time, time.perf_counter())


def schedule_bar(
    port: mido.ports.BaseOutput,
    bar: BarInfo,
    events: BarEvents,
    on_send: SendObserver | None = None,
) -> asyncio.Task[None]:
    return asyncio.create_task(play_bar(port, bar, events, on_send))
