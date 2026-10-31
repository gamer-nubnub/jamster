"""Branching-score unit tests (schema v0, section mode, minimal triggers)."""

import pytest

from jamster.score import Score, ScoreState, Trigger

RAW = {
    "score_id": "s-1",
    "ttl_bars": 8,
    "current": "verse",
    "say": {"Roots": "Want a key change?"},
    "sections": {
        "verse": {
            "chords": ["Cmaj", "Fmaj"],
            "agents": {"Roots": {"intent": "roots", "density": 0.4, "energy": 0.4},
                       "Sticks": {"intent": "straight", "density": 0.4, "energy": 0.4}},
            "transitions": [
                {"to": "chorus", "when": {"energy_above": 0.7, "for_bars": 2}},
                {"to": "verse_am", "when": {"chord": "Amin"}},
            ],
        },
        "verse_am": {
            "agents": {"Roots": {"intent": "A root"}, "Sticks": {"intent": "drop hats"}},
            "transitions": [{"to": "verse", "when": {"chord": "Cmaj"}}],
        },
        "chorus": {
            "agents": {"Roots": {"intent": "drive", "density": 0.8}, "Sticks": {"intent": "open hats"}},
            "transitions": [{"to": "verse", "when": {"energy_below": 0.4, "for_bars": 2}}],
        },
    },
}


def test_from_dict_roundtrip_and_validation():
    score = Score.from_dict(RAW, issued_at_bar=16)
    assert score.expires_at_bar() == 24
    assert score.sections["verse"].transitions[1].when.chord == "Amin"
    assert score.say == {"Roots": "Want a key change?"}
    with pytest.raises(ValueError):
        Score.from_dict({**RAW, "current": "bridge"})
    bad = {**RAW, "sections": {"a": {"agents": {}, "transitions": [{"to": "zzz", "when": {}}]}}}
    with pytest.raises(ValueError):
        Score.from_dict(bad)


def test_trigger_matching():
    assert Trigger(chord="Amin").matches([("Amin7", 0.5)])
    assert Trigger(chord="A").matches([("Amin", 0.5)])
    assert not Trigger(chord="A").matches([("A#maj", 0.5)])
    assert not Trigger(energy_above=0.7, for_bars=2).matches([("C", 0.2), ("C", 0.9)])
    assert Trigger(energy_above=0.7, for_bars=2).matches([("C", 0.8), ("C", 0.9)])


def test_state_switches_sections_in_order():
    state = ScoreState()
    state.accept(Score.from_dict(RAW, issued_at_bar=0), 0)
    assert state.advance(0, "Cmaj", 0.3).name == "verse"
    assert state.advance(1, "Amin", 0.3).name == "verse_am"
    assert state.advance(2, "Cmaj", 0.3).name == "verse"
    assert state.advance(3, "Cmaj", 0.9).name == "verse"  # for_bars 2: not yet
    assert state.advance(4, "Cmaj", 0.9).name == "chorus"
    assert [s[2] for s in state.switches] == ["verse_am", "verse", "chorus"]
    assert not state.holding


def test_state_holds_on_ttl_and_unanticipated_chords():
    state = ScoreState(no_match_bars=2)
    state.accept(Score.from_dict(RAW, issued_at_bar=0), 0)
    state.advance(0, "Cmaj", 0.3)
    state.advance(1, "Ebmaj", 0.3)
    assert not state.holding  # one surprise bar is fine
    state.advance(2, "Ebmaj", 0.3)
    assert state.holding and "not anticipated" in state.holding_reason
    state.advance(3, "Cmaj", 0.3)
    assert not state.holding  # recovered
    state.advance(8, "Cmaj", 0.3)
    assert state.holding and "expired" in state.holding_reason
    state.accept(Score.from_dict({**RAW, "score_id": "s-2"}, issued_at_bar=8), 8)
    assert not state.holding


def test_before_any_score_groove_follows_rather_than_loops():
    state = ScoreState()
    assert state.advance(0, "Cmaj", 0.0) is None
    assert state.holding and not state.loop


def test_anticipated_sections_cover_transition_targets():
    state = ScoreState()
    state.accept(Score.from_dict(RAW), 0)
    assert [s.name for s in state.anticipated_sections()] == ["verse", "chorus", "verse_am"]
