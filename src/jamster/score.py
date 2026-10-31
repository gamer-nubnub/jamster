"""Branching score (schema v0, ``section`` granularity, ``minimal`` triggers).

The producer emits a ``Score``; ``ScoreState`` advances it bar by bar on the
conductor's hot path with no LLM involved: evaluate the current section's
transitions in order, switch on the first match, and flag the band as
*holding* when the score is stale (TTL expired) or the player has been
outside anything the score anticipates for too long.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 0
DEFAULT_TTL_BARS = 8
DEFAULT_NO_MATCH_BARS = 2


@dataclass(frozen=True)
class Part:
    """What one agent should do while a section is active."""

    intent: str
    density: float = 0.4
    energy: float = 0.4


@dataclass(frozen=True)
class Trigger:
    chord: str | None = None
    energy_above: float | None = None
    energy_below: float | None = None
    for_bars: int = 1

    def matches_bar(self, chord: str, energy: float) -> bool:
        if self.chord is not None and not chord_matches(self.chord, chord):
            return False
        if self.energy_above is not None and energy <= self.energy_above:
            return False
        if self.energy_below is not None and energy >= self.energy_below:
            return False
        return True

    def matches(self, history: list[tuple[str, float]]) -> bool:
        """True if the last ``for_bars`` bars of (chord, energy) all match."""
        if len(history) < self.for_bars:
            return False
        return all(self.matches_bar(chord, energy) for chord, energy in history[-self.for_bars :])


@dataclass(frozen=True)
class Transition:
    to: str
    when: Trigger


@dataclass(frozen=True)
class Section:
    name: str
    agents: dict[str, Part]
    transitions: list[Transition] = field(default_factory=list)
    chords: list[str] = field(default_factory=list)  # chords this section expects (optional)

    def anticipates(self, chord: str) -> bool:
        """Does the score know what to do when the player is on ``chord``?"""
        if not self.chords and not any(t.when.chord for t in self.transitions):
            return True  # section makes no chord claims → anything goes
        if any(chord_matches(c, chord) for c in self.chords):
            return True
        return any(t.when.chord and chord_matches(t.when.chord, chord) for t in self.transitions)


@dataclass(frozen=True)
class Score:
    score_id: str
    issued_at_bar: int
    current: str
    sections: dict[str, Section]
    ttl_bars: int = DEFAULT_TTL_BARS
    say: dict[str, str] = field(default_factory=dict)
    version: int = SCHEMA_VERSION

    def expires_at_bar(self) -> int:
        return self.issued_at_bar + self.ttl_bars

    @staticmethod
    def from_dict(data: dict[str, Any], issued_at_bar: int | None = None) -> Score:
        sections: dict[str, Section] = {}
        for name, raw in dict(data["sections"]).items():
            agents = {
                agent: Part(
                    intent=str(part.get("intent", "")),
                    density=_unit(part.get("density", 0.4)),
                    energy=_unit(part.get("energy", 0.4)),
                )
                for agent, part in dict(raw.get("agents", {})).items()
            }
            transitions = [
                Transition(to=str(t["to"]), when=_trigger(t.get("when", {})))
                for t in list(raw.get("transitions", []))
            ]
            sections[name] = Section(name, agents, transitions, [str(c) for c in raw.get("chords", [])])
        if not sections:
            raise ValueError("score has no sections")
        current = str(data.get("current") or next(iter(sections)))
        if current not in sections:
            raise ValueError(f"current section {current!r} not in sections")
        for section in sections.values():
            for t in section.transitions:
                if t.to not in sections:
                    raise ValueError(f"transition to unknown section {t.to!r}")
        return Score(
            score_id=str(data.get("score_id", "s-0")),
            issued_at_bar=int(issued_at_bar if issued_at_bar is not None else data.get("issued_at_bar", 0)),
            current=current,
            sections=sections,
            ttl_bars=max(1, int(data.get("ttl_bars", DEFAULT_TTL_BARS))),
            say={str(k): str(v) for k, v in dict(data.get("say", {})).items()},
            version=int(data.get("version", SCHEMA_VERSION)),
        )


# JSON schema handed to the LLM as ``response_schema``.
SCORE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["score_id", "ttl_bars", "current", "sections"],
    "properties": {
        "version": {"type": "integer"},
        "score_id": {"type": "string"},
        "ttl_bars": {"type": "integer", "minimum": 1, "maximum": 64},
        "current": {"type": "string"},
        "say": {"type": "object", "additionalProperties": {"type": "string"}},
        "sections": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {
                "type": "object",
                "required": ["agents"],
                "properties": {
                    "chords": {"type": "array", "items": {"type": "string"}},
                    "agents": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "required": ["intent"],
                            "properties": {
                                "intent": {"type": "string"},
                                "density": {"type": "number", "minimum": 0, "maximum": 1},
                                "energy": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                        },
                    },
                    "transitions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["to", "when"],
                            "properties": {
                                "to": {"type": "string"},
                                "when": {
                                    "type": "object",
                                    "properties": {
                                        "chord": {"type": "string"},
                                        "energy_above": {"type": "number"},
                                        "energy_below": {"type": "number"},
                                        "for_bars": {"type": "integer", "minimum": 1},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


@dataclass
class ScoreState:
    """Hot-path tracker: which section is active and whether we are holding."""

    ttl_default: int = DEFAULT_TTL_BARS
    no_match_bars: int = DEFAULT_NO_MATCH_BARS
    score: Score | None = None
    section_name: str | None = None
    history: list[tuple[str, float]] = field(default_factory=list)
    unanticipated_bars: int = 0
    holding_reason: str | None = "no score yet"
    switches: list[tuple[int, str, str]] = field(default_factory=list)  # (bar, from, to)

    @property
    def holding(self) -> bool:
        return self.holding_reason is not None

    @property
    def loop(self) -> bool:
        """Hold-and-loop applies once a plan existed and went stale; before the
        first plan the groove engine simply follows the player."""
        return self.holding and self.score is not None

    @property
    def section(self) -> Section | None:
        if self.score is None or self.section_name is None:
            return None
        return self.score.sections.get(self.section_name)

    def accept(self, score: Score, bar_index: int) -> None:
        """Install a fresh score; keep the current section name if it survives."""
        self.score = score
        if self.section_name not in score.sections:
            self.section_name = score.current
        self.unanticipated_bars = 0
        self.holding_reason = None

    def advance(self, bar_index: int, chord: str, energy: float) -> Section | None:
        """Called once per bar boundary before scheduling; returns active section."""
        self.history.append((chord, energy))
        del self.history[:-16]
        if self.score is None or self.section is None:
            self.holding_reason = "no score yet"
            return None

        section = self.section
        for transition in section.transitions:
            if transition.when.matches(self.history):
                self.switches.append((bar_index, section.name, transition.to))
                self.section_name = transition.to
                section = self.score.sections[transition.to]
                break

        if section.anticipates(chord):
            self.unanticipated_bars = 0
        else:
            self.unanticipated_bars += 1

        if bar_index >= self.score.expires_at_bar():
            self.holding_reason = f"score {self.score.score_id} expired at bar {self.score.expires_at_bar() + 1}"
        elif self.unanticipated_bars >= self.no_match_bars:
            self.holding_reason = f"{chord} not anticipated for {self.unanticipated_bars} bars"
        else:
            self.holding_reason = None
        return section

    def anticipated_sections(self) -> list[Section]:
        """Current section plus every transition target (for pre-generation)."""
        return [section for section, _ in self.anticipated()]

    def anticipated(self) -> list[tuple[Section, list[str]]]:
        """(section, chord names to prepare it for): the current section and each
        transition target, keyed by the chords the score says to expect there."""
        if self.score is None or self.section is None:
            return []
        current = self.section
        plan: dict[str, list[str]] = {current.name: list(current.chords)}
        for t in current.transitions:
            chords = plan.setdefault(t.to, list(self.score.sections[t.to].chords))
            if t.when.chord and t.when.chord not in chords:
                chords.insert(0, t.when.chord)
        return [(self.score.sections[name], chords) for name, chords in plan.items()]


def _unit(value: Any) -> float:
    return min(1.0, max(0.0, float(value)))


def _trigger(raw: dict[str, Any]) -> Trigger:
    return Trigger(
        chord=str(raw["chord"]) if raw.get("chord") else None,
        energy_above=float(raw["energy_above"]) if raw.get("energy_above") is not None else None,
        energy_below=float(raw["energy_below"]) if raw.get("energy_below") is not None else None,
        for_bars=max(1, int(raw.get("for_bars", 1))),
    )


def chord_matches(pattern: str, chord: str) -> bool:
    """``Amin`` matches ``Amin`` and ``Amin7``; ``A`` matches any A chord."""
    p, c = pattern.strip().lower(), chord.strip().lower()
    if c == p:
        return True
    if not c.startswith(p):
        return False
    return len(p) > 1 or c[1:2] != "#"
