"""Tier 2 producer: asks the strategic model for a branching score.

Runs off the hot path. The conductor calls ``maybe_request`` each bar; a
request is fired at most once at a time, on a cadence (``every_bars``) and
again shortly before the current score expires so the next one lands in time
(anticipation). Results are picked up with ``take``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from .agents import SessionConfig
from .llm import LLMBackend
from .score import SCORE_JSON_SCHEMA, Score

SYSTEM_PROMPT = """You are the producer of a live band of AI session musicians jamming
with a human player. You do not play; you plan. Every few bars you write a
*branching score*: a small set of named sections, what each agent should do
in each, and the triggers that move the band between sections based on what
the human plays (their chord, their energy 0-1).

Rules:
- Output ONLY a JSON object matching the schema. No prose.
- Keep 2-4 sections. Names are short lowercase identifiers.
- Each section lists every agent by name with an `intent` (one line, concrete,
  musician-to-musician), `density` and `energy` in 0-1.
- `chords`: chord names the section expects (e.g. "Cmaj", "Amin", "G7").
- Transitions are evaluated in order each bar; first match wins. Triggers may
  use `chord`, `energy_above`, `energy_below`, and `for_bars`.
- Anticipate: include a section for the most likely next thing the player does.
- `current` is the section to play now. `ttl_bars` is how long this plan is
  valid (8 is normal).
- Optional `say`: one short sentence per agent, only if worth saying.
"""


@dataclass
class ProducerContext:
    bar_index: int
    chord: str
    energy: float
    history: list[tuple[str, float]]
    transcript: list[str] = field(default_factory=list)
    active_section: str | None = None
    previous_score: Score | None = None


@dataclass
class Producer:
    backend: LLMBackend
    session: SessionConfig
    model: str
    every_bars: int = 8
    refresh_lead_bars: int = 2  # re-plan this many bars before the TTL runs out
    timeout: float = 45.0
    _task: asyncio.Task[Score | None] | None = None
    _ready: Score | None = None
    requests: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def inflight(self) -> bool:
        return self._task is not None and not self._task.done()

    def maybe_request(self, context: ProducerContext, force: bool = False) -> bool:
        if self.inflight:
            return False
        due = force or context.previous_score is None or context.bar_index % self.every_bars == 0
        if context.previous_score is not None:
            due = due or context.bar_index >= context.previous_score.expires_at_bar() - self.refresh_lead_bars
        if not due:
            return False
        self._task = asyncio.create_task(self._request(context))
        self.requests += 1
        return True

    def take(self) -> Score | None:
        """Return a finished score once, if any."""
        if self._task is not None and self._task.done():
            try:
                self._ready = self._task.result()
            except Exception as error:  # noqa: BLE001 - surfaced via failures
                self.failures.append(repr(error))
            self._task = None
        score, self._ready = self._ready, None
        return score

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None

    async def wait(self) -> Score | None:
        if self._task is not None:
            await asyncio.shield(self._task)
        return self.take()

    def prompt_for(self, context: ProducerContext) -> str:
        agents = "\n".join(
            f"- {a.name} ({a.role}): {a.personality or 'no personality given'}" for a in self.session.agents
        )
        history = ", ".join(f"{c}@{e:.1f}" for c, e in context.history[-8:]) or "(nothing yet)"
        transcript = "\n".join(f"  > {line}" for line in context.transcript[-6:]) or "  (silence)"
        previous = (
            json.dumps(_score_summary(context.previous_score)) if context.previous_score else "none"
        )
        return (
            f"Session: {self.session.name}, {self.session.bpm:.0f} bpm, {self.session.beats_per_bar}/4.\n"
            f"Session brief: {self.session.context_prompt or '(none)'}\n"
            f"Agents:\n{agents}\n"
            f"Now at bar {context.bar_index + 1}. Player chord: {context.chord}, energy {context.energy:.2f}.\n"
            f"Recent bars (chord@energy): {history}\n"
            f"Active section: {context.active_section or 'none'}\n"
            f"What the player said recently:\n{transcript}\n"
            f"Previous plan: {previous}\n"
            f"Write the next branching score as JSON. Use agent names exactly: "
            f"{', '.join(a.name for a in self.session.agents)}."
        )

    async def _request(self, context: ProducerContext) -> Score | None:
        completion = await self.backend.complete(
            role="producer",
            system=SYSTEM_PROMPT,
            prompt=self.prompt_for(context),
            model=self.model,
            schema=SCORE_JSON_SCHEMA,
            timeout=self.timeout,
        )
        data = completion.json()
        score = Score.from_dict(data, issued_at_bar=context.bar_index)
        missing = {a.name for a in self.session.agents} - set().union(*(s.agents for s in score.sections.values()))
        if missing:
            raise ValueError(f"score omits agents: {sorted(missing)}")
        return score


def _score_summary(score: Score) -> dict:
    return {
        "score_id": score.score_id,
        "current": score.current,
        "sections": {
            name: {"chords": s.chords, "transitions": [t.to for t in s.transitions]}
            for name, s in score.sections.items()
        },
    }
