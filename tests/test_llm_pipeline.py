"""Functional tests for the LLM pipeline on a scripted backend.

Producer → branching score → transitions on the hot path → performer bars
from the model (cached, pre-generated) with groove fallback and hold-and-loop,
all on fake MIDI ports at a fast tempo.
"""

import asyncio
import json

import pytest

from jamster.conductor import Conductor
from jamster.llm import FakeBackend
from jamster.performer import parse_bar
from jamster.testing import FakePorts, fast_clock

BPM = 1500.0
BAR_SECONDS = 4 * 60.0 / BPM

SCORE = {
    "score_id": "s-1",
    "ttl_bars": 8,
    "current": "verse",
    "say": {"Roots": "Let's keep it mellow."},
    "sections": {
        "verse": {
            "chords": ["Cmaj"],
            "agents": {"Roots": {"intent": "roots", "density": 0.4}, "Sticks": {"intent": "straight", "density": 0.4}},
            "transitions": [{"to": "verse_am", "when": {"chord": "Amin"}}],
        },
        "verse_am": {
            "chords": ["Amin"],
            "agents": {"Roots": {"intent": "A pedal", "density": 0.3}, "Sticks": {"intent": "no hats", "density": 0.2}},
            "transitions": [{"to": "verse", "when": {"chord": "Cmaj"}}],
        },
    },
}

# Model bars are recognisable: bass always plays note 40 (E2) so we can tell them apart from groove.
MODEL_BASS_NOTE = 40


def bar_response(role: str, prompt: str) -> str:
    if role == "producer":
        return json.dumps(SCORE)
    if role == "performer:bass":
        return json.dumps({"notes": [{"beat": 0, "note": MODEL_BASS_NOTE, "velocity": 100, "duration": 2}]})
    return json.dumps({"notes": [{"beat": 0, "note": 36, "velocity": 110, "duration": 0.1},
                                 {"beat": 2, "note": 38, "velocity": 100, "duration": 0.1}]})


def make_conductor(session, ports, backend) -> Conductor:
    return Conductor(
        session,
        backend=backend,
        open_output=ports.open_output,
        open_input=ports.open_input,
        make_clock=fast_clock(BPM),
        log=lambda _msg: None,
    )


@pytest.mark.asyncio
async def test_e2e_plan_then_model_bars_then_transition(session):
    ports = FakePorts()
    backend = FakeBackend(responder=bar_response, delay=BAR_SECONDS * 0.3)
    conductor = make_conductor(session, ports, backend)

    async def player() -> None:
        ports.input.play_chord([60, 64, 67])
        await asyncio.sleep(BAR_SECONDS * 5.5)
        ports.input.release_all([60, 64, 67])
        ports.input.play_chord([57, 60, 64])

    await asyncio.gather(conductor.run(bars=10), player())

    roles = {c.role for c in backend.calls}
    assert roles == {"producer", "performer:bass", "performer:drums"}
    assert backend.started is False  # stopped cleanly

    sections = [b.section for b in conductor.bars]
    assert sections[0] is None  # plan not back yet at bar 1
    assert "verse" in sections and "verse_am" in sections
    assert conductor.state.switches and conductor.state.switches[0][2] == "verse_am"
    assert conductor.messages == [(1, "Roots", "Let's keep it mellow.")]  # repeated say is deduped

    bass_sources = [b.sources["Roots"] for b in conductor.bars]
    assert bass_sources[0] == "groove"  # no plan, no cache: deterministic fallback
    assert "model" in bass_sources
    # Once verse_am was anticipated, its bar was pre-generated: the first bar after
    # the switch is already served from the model, not the fallback.
    switch_bar = conductor.state.switches[0][0]
    assert conductor.bars[switch_bar].sources["Roots"] == "model", bass_sources

    bass_notes = [m.note for m in ports.outputs["bass"].note_ons()]
    assert 48 in bass_notes and MODEL_BASS_NOTE in bass_notes
    assert not conductor.state.holding
    assert conductor.performer.failures == []
    assert backend.spend.calls == len(backend.calls)


@pytest.mark.asyncio
async def test_e2e_hold_and_loop_when_plan_expires(session):
    ports = FakePorts()
    short = {**SCORE, "ttl_bars": 3}
    calls = {"producer": 0}

    async def responder(role: str, prompt: str) -> str:
        if role == "producer":
            calls["producer"] += 1
            if calls["producer"] > 1:
                await asyncio.sleep(BAR_SECONDS * 20)  # producer "goes away"
            return json.dumps(short)
        return bar_response(role, prompt)

    backend = FakeBackend(responder=responder)
    conductor = make_conductor(session, ports, backend)
    ports.input.play_chord([60, 64, 67])
    await conductor.run(bars=8)

    holding = [b.holding_reason for b in conductor.bars]
    assert any(reason and "expired" in reason for reason in holding), holding
    looped = [b for b in conductor.bars if b.holding_reason and "expired" in b.holding_reason]
    assert all(b.sources == {"Roots": "loop", "Sticks": "loop"} for b in looped)
    assert conductor.producer.inflight is False  # drained on shutdown


@pytest.mark.asyncio
async def test_e2e_bad_model_output_falls_back_to_groove(session):
    ports = FakePorts()

    def responder(role: str, prompt: str) -> str:
        if role == "producer":
            return json.dumps(SCORE)
        return "sorry, I cannot play right now"

    backend = FakeBackend(responder=responder)
    conductor = make_conductor(session, ports, backend)
    ports.input.play_chord([60, 64, 67])
    await conductor.run(bars=4)

    assert all(src == "groove" for b in conductor.bars for src in b.sources.values())
    assert conductor.performer.failures  # recorded, not raised
    assert all(port.note_ons() for port in ports.outputs.values())


@pytest.mark.asyncio
async def test_producer_rejects_scores_missing_agents(session):
    def responder(role: str, prompt: str) -> str:
        if role == "producer":
            partial = json.loads(json.dumps(SCORE))
            for s in partial["sections"].values():
                s["agents"].pop("Sticks")
            return json.dumps(partial)
        return bar_response(role, prompt)

    ports = FakePorts()
    conductor = make_conductor(session, ports, FakeBackend(responder=responder))
    await conductor.run(bars=3)
    assert conductor.state.score is None
    assert conductor.producer.failures and "omits agents" in conductor.producer.failures[0]


def test_parse_bar_validates_and_clamps(session):
    bass = session.agents[0]
    events = parse_bar(
        {"notes": [
            {"beat": 0, "note": 48, "velocity": 300, "duration": 1},
            {"beat": 5, "note": 48, "velocity": 90, "duration": 1},      # beat out of bar
            {"beat": 1, "note": 200, "velocity": 90, "duration": 1},     # bad note
            {"beat": 2, "note": "x", "velocity": 90, "duration": 1},     # garbage
        ], "cc": [{"beat": 0, "controller": 11, "value": 999}]},
        bass, 4,
    )
    assert len(events) == 2
    assert events[0].velocity == 127 and events[1].value == 127
    drums = parse_bar({"notes": [{"beat": 0, "note": 36, "velocity": 100, "duration": 0.1}]}, session.agents[1], 4)
    assert drums[0].channel == 9


def test_producer_prompt_mentions_agents_and_history(session):
    from jamster.producer import Producer, ProducerContext

    producer = Producer(FakeBackend(), session, "fake-smart")
    prompt = producer.prompt_for(ProducerContext(bar_index=7, chord="Cmaj", energy=0.5,
                                                 history=[("Cmaj", 0.4), ("Amin", 0.6)],
                                                 transcript=["more energy please"]))
    assert "Roots (bass)" in prompt and "Sticks (drums)" in prompt
    assert "Amin@0.6" in prompt and "more energy please" in prompt and "bar 8" in prompt
