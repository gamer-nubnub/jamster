"""Functional end-to-end tests: the whole Phase 0/1 pipeline on fake ports.

These run the real Conductor loop (clock → chord tracker → groove engine →
scheduler → port) at a fast tempo, so they verify the phase acceptance
criteria from the README without loopMIDI or Ableton.
"""

import asyncio
import statistics

import pytest

from jamster.clock import BarInfo, InternalClock
from jamster.conductor import Conductor
from jamster.events import NoteEvent
from jamster.scheduler import play_bar
from jamster.testing import FakeOutput, FakePorts, fast_clock

JITTER_BUDGET_SECONDS = 0.010  # Phase 0 target: < 10 ms


async def _run(session, ports: FakePorts, bars: int, bpm: float = 3000.0) -> Conductor:
    conductor = Conductor(
        session,
        open_output=ports.open_output,
        open_input=ports.open_input,
        make_clock=fast_clock(bpm),
    )
    await conductor.run(bars=bars)
    return conductor


@pytest.mark.asyncio
async def test_phase0_scheduled_metronome_jitter():
    """Phase 0: scheduled events land within the jitter budget."""
    clock = InternalClock(bpm=600, beats_per_bar=4)
    port = FakeOutput()
    errors: list[float] = []
    click = [NoteEvent(beat=b, note=37, velocity=100, duration_beats=0.1, channel=9) for b in range(4)]
    for _ in range(8):
        bar = await clock.wait_for_bar_start()
        await play_bar(port, bar, click, on_send=lambda target, actual: errors.append(actual - target))

    assert len(errors) == 8 * 4 * 2  # note on + off per click
    assert max(errors) < JITTER_BUDGET_SECONDS, f"max jitter {max(errors) * 1000:.2f} ms"
    assert statistics.mean(errors) < JITTER_BUDGET_SECONDS / 2


@pytest.mark.asyncio
async def test_phase1_band_plays_every_bar(session):
    """Every agent gets its own port and every bar produces notes on each."""
    ports = FakePorts()
    conductor = await _run(session, ports, bars=4)

    assert set(ports.outputs) == {"bass", "drums"}
    assert [index for index, _ in conductor.bar_log] == [0, 1, 2, 3]
    bass, drums = ports.outputs["bass"], ports.outputs["drums"]
    assert len(bass.note_ons()) == 4 * 3  # density 0.4 → 3 bass notes per bar
    assert all(m.channel == 9 for m in drums.note_ons())
    assert bass.closed and drums.closed


@pytest.mark.asyncio
async def test_phase1_bass_follows_user_chord_within_one_bar(session):
    """Phase 1 criterion: a chord change is followed by the next bar."""
    ports = FakePorts()
    bpm = 3000.0
    bar_seconds = 4 * 60.0 / bpm

    async def player() -> None:
        ports.input.play_chord([60, 64, 67])  # C major
        await asyncio.sleep(bar_seconds * 2.5)  # mid bar 3
        ports.input.release_all([60, 64, 67])
        ports.input.play_chord([57, 60, 64])  # A minor

    conductor, _ = await asyncio.gather(_run(session, ports, bars=6, bpm=bpm), player())

    chords = [name for _, name in conductor.bar_log]
    assert chords[:3] == ["Cmaj", "Cmaj", "Cmaj"]
    # Released C-E-G stays in the recent-note window, so Amin7 is a valid estimate.
    assert all(name.startswith("Amin") for name in chords[3:]), chords

    bass_roots = [m.note for m in ports.outputs["bass"].note_ons() if m.note in (48, 57)]
    assert 48 in bass_roots and 57 in bass_roots  # C then A root
    assert bass_roots.index(57) > bass_roots.index(48)


@pytest.mark.asyncio
async def test_demo_session_runs_end_to_end(demo_session):
    """The shipped demo config loads and drives both agents."""
    ports = FakePorts()
    conductor = await _run(demo_session, ports, bars=2)
    assert len(conductor.bar_log) == 2
    assert {agent.port for agent in demo_session.agents} == set(ports.outputs)
    assert all(port.note_ons() for port in ports.outputs.values())


def test_bar_info_is_immutable():
    bar = BarInfo(index=0, start_time=0.0, seconds_per_beat=0.5)
    with pytest.raises(Exception):
        bar.index = 1  # type: ignore[misc]
