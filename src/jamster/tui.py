"""Terminal dashboard: a Rich live view of what the band is doing.

A CLI stand-in for UI v1 (README "UI v1 spec") with the same anatomy:
header, section timeline, agent cards, producer card, message log, status
strip. Renders once per bar from the conductor's ``on_bar`` hook, so it adds
nothing to the hot path beyond building a few strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from .conductor import BarRecord, Conductor

# Pastel accents; per-project theming comes with the web UI.
AGENT_COLOURS = ["#8fd3ff", "#ffb3c6", "#c3f0a6", "#ffd9a0", "#d5b8ff", "#a0f0e0"]
PRODUCER_COLOUR = "#f5e663"
DIM = "grey50"
TIMELINE_BARS = 32


@dataclass
class Dashboard:
    """Builds a renderable from the conductor state after each bar."""

    conductor: "Conductor"
    colours: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for i, agent in enumerate(self.conductor.session.agents):
            self.colours.setdefault(agent.name, AGENT_COLOURS[i % len(AGENT_COLOURS)])

    # ---------------------------------------------------------------- pieces

    def header(self, bar: "BarRecord") -> Text:
        c, s = self.conductor, self.conductor.session
        t = Text()
        t.append(f" {s.name} ", style="bold")
        t.append(f"{s.bpm:.0f} bpm  {s.beats_per_bar}/4  ", style=DIM)
        t.append(f"bar {bar.index + 1:>4}  ", style="bold")
        t.append(f"{bar.chord:<7}", style="bold cyan")
        t.append("energy ", style=DIM)
        t.append(_meter(bar.energy, 10))
        t.append(f"  llm {'on' if c.backend else 'off'}", style=DIM)
        return t

    def timeline(self) -> Panel:
        bars = self.conductor.bars[-TIMELINE_BARS:]
        line = Text()
        for b in bars:
            if b.holding_reason and b.score_id:
                line.append("▒", style="red")
            elif b.section is None:
                line.append("·", style=DIM)
            else:
                line.append("█", style=_section_style(b.section))
        line.append("▏", style="bold white")  # playhead
        state = self.conductor.state
        for section, chords in state.anticipated()[1:]:
            label = f" {section.name}" + (f"({','.join(chords)})" if chords else "")
            line.append("░░", style=f"dim {_section_style(section.name)}")
            line.append(label, style=DIM)
        if state.score is not None:
            remaining = state.score.expires_at_bar() - (bars[-1].index if bars else 0)
            line.append(f"   ttl {max(0, remaining)} bars", style=DIM)
        legend = Text("  ".join(f"{name}" for name in (state.score.sections if state.score else [])), style=DIM)
        for name in state.score.sections if state.score else []:
            legend.highlight_words([name], style=_section_style(name))
        return Panel(Group(line, legend), title="timeline", border_style=DIM, padding=(0, 1))

    def agent_cards(self, bar: "BarRecord") -> Columns:
        cards = []
        section = self.conductor.state.section
        for agent in self.conductor.session.agents:
            colour = self.colours[agent.name]
            part = section.agents.get(agent.name) if section else None
            body = Text()
            body.append(f"{agent.role}\n", style=DIM)
            body.append((part.intent if part else "groove engine") + "\n")
            density = part.density if part else agent.density
            body.append("density ", style=DIM)
            body.append(_meter(density, 8))
            body.append("  src ", style=DIM)
            body.append(bar.sources.get(agent.name, "-"), style=_source_style(bar.sources.get(agent.name, "")))
            last = next((m for m in reversed(self.conductor.messages) if m[1] == agent.name), None)
            if last is not None and bar.index - last[0] < 8:
                body.append(f"\n“{last[2]}”", style=f"italic {colour}")
            cards.append(Panel(body, title=Text(agent.name, style=f"bold {colour}"), border_style=colour, padding=(0, 1)))
        cards.append(self.producer_card(bar))
        return Columns(cards, expand=True, equal=True)

    def producer_card(self, bar: "BarRecord") -> Panel:
        c = self.conductor
        body = Text()
        if c.producer is None:
            body.append("off\n", style=DIM)
            body.append("deterministic band", style=DIM)
        else:
            state = "thinking…" if c.producer.inflight else "idle"
            body.append(f"{state}\n", style="bold" if c.producer.inflight else DIM)
            body.append(f"plans {len(c.plans)}  requests {c.producer.requests}  failed {len(c.producer.failures)}\n", style=DIM)
            if c.state.score:
                body.append(f"score {c.state.score.score_id}  section ", style=DIM)
                body.append(c.state.section_name or "-", style=_section_style(c.state.section_name or ""))
            if c.producer.failures:
                body.append(f"\n{c.producer.failures[-1][:60]}", style="red")
        return Panel(body, title=Text("producer", style=f"bold {PRODUCER_COLOUR}"), border_style=PRODUCER_COLOUR, padding=(0, 1))

    def log(self) -> Panel:
        table = Table.grid(padding=(0, 1))
        rows = [(b, a, t) for b, a, t in self.conductor.messages][-6:]
        rows += [(b, "you", t) for b, t in enumerate(self.conductor.transcript)][-3:]
        for b, who, text in sorted(rows)[-8:]:
            table.add_row(Text(f"{b + 1:>4}", style=DIM), Text(who, style=f"bold {self.colours.get(who, 'white')}"), Text(text))
        if not rows:
            table.add_row(Text("(no messages yet)", style=DIM))
        return Panel(table, title="log", border_style=DIM, padding=(0, 1))

    def status(self, bar: "BarRecord") -> Text:
        c = self.conductor
        t = Text(" ")
        if c.state.loop:
            t.append(" HOLDING ", style="bold black on red")
            t.append(f" {bar.holding_reason}", style="red")
        elif bar.section is None:
            t.append(" GROOVE ", style="bold black on grey70")
            t.append(" waiting for first plan" if c.producer else " deterministic", style=DIM)
        else:
            t.append(" LIVE ", style="bold black on green")
            t.append(f" {bar.section}", style=_section_style(bar.section))
        clock = "link" if c.use_link else "internal"
        t.append(f"   clock {clock}", style=DIM)
        if c.backend is not None:
            sp = c.backend.spend
            cost = f"${sp.total_cost:.3f}" + ("?" if sp.unknown_cost_calls else "")
            t.append(f"   calls {sp.calls}  spend {cost}", style=DIM)
            perf = c.performer
            t.append(f"   bars model/groove {perf.served_from_model}/{perf.served_from_fallback}  inflight {perf.inflight}", style=DIM)
        return t

    def render(self, bar: "BarRecord") -> Group:
        return Group(self.header(bar), self.timeline(), self.agent_cards(bar), self.log(), self.status(bar))


class CliUI:
    """Attach to a Conductor via ``on_bar``; drives a Rich ``Live`` display."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._live: Live | None = None
        self._dashboard: Dashboard | None = None

    def on_bar(self, conductor: "Conductor", bar: "BarRecord") -> None:
        if self._dashboard is None:
            self._dashboard = Dashboard(conductor)
        if self._live is None:
            self._live = Live(console=self.console, refresh_per_second=8, transient=False)
            self._live.start()
        self._live.update(self._dashboard.render(bar))

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None


def _meter(value: float, width: int) -> Text:
    filled = round(min(1.0, max(0.0, value)) * width)
    colour = "green" if value < 0.5 else "yellow" if value < 0.8 else "red"
    t = Text("▮" * filled, style=colour)
    t.append("▯" * (width - filled), style=DIM)
    return t


def _section_style(name: str) -> str:
    palette = ["#8fd3ff", "#ffb3c6", "#c3f0a6", "#ffd9a0", "#d5b8ff", "#a0f0e0"]
    return palette[sum(map(ord, name)) % len(palette)] if name else DIM


def _source_style(source: str) -> str:
    return {"model": "bold green", "groove": "yellow", "loop": "red"}.get(source, DIM)
