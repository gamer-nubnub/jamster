"""Agent and session configuration loading.

Sessions reference agent files by name; agent YAMLs live in ``config/agents/``.
Personality and context prompts are inert in Phase 1 and become model context
in Phases 2-3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AgentConfig:
    name: str
    role: str  # "bass" | "drums" (more roles with Phase 2)
    port: str  # loopMIDI output port name fragment
    channel: int
    density: float
    personality: str = ""


DEFAULT_PRODUCER_MODEL = "claude-sonnet-4.5"
DEFAULT_PERFORMER_MODEL = "gpt-4.1"


@dataclass(frozen=True)
class ModelsConfig:
    producer: str = DEFAULT_PRODUCER_MODEL
    performer: str = DEFAULT_PERFORMER_MODEL
    producer_every_bars: int = 8


@dataclass(frozen=True)
class SessionConfig:
    name: str
    bpm: float
    beats_per_bar: int
    input_port: str  # user MIDI input fragment; empty = first available
    context_prompt: str
    agents: list[AgentConfig] = field(default_factory=list)
    models: ModelsConfig = field(default_factory=ModelsConfig)


def load_agent(path: Path) -> AgentConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AgentConfig(
        name=data["name"],
        role=data["role"],
        port=data["port"],
        channel=int(data.get("channel", 0)),
        density=float(data.get("density", 0.4)),
        personality=str(data.get("personality", "")).strip(),
    )


def load_session(path: Path) -> SessionConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    agents_dir = path.parent.parent / "agents"
    agents = [load_agent(agents_dir / agent_file) for agent_file in data.get("agents", [])]
    models = dict(data.get("models") or {})
    return SessionConfig(
        name=data["name"],
        bpm=float(data.get("bpm", 100)),
        beats_per_bar=int(data.get("beats_per_bar", 4)),
        input_port=str(data.get("input_port", "")),
        context_prompt=str(data.get("context_prompt", "")).strip(),
        agents=agents,
        models=ModelsConfig(
            producer=str(models.get("producer", DEFAULT_PRODUCER_MODEL)),
            performer=str(models.get("performer", DEFAULT_PERFORMER_MODEL)),
            producer_every_bars=int(models.get("producer_every_bars", 8)),
        ),
    )
