from pathlib import Path

import pytest

from jamster.agents import AgentConfig, SessionConfig, load_session
from jamster.testing import FakePorts

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def ports() -> FakePorts:
    return FakePorts()


@pytest.fixture
def session() -> SessionConfig:
    return SessionConfig(
        name="test",
        bpm=100,
        beats_per_bar=4,
        input_port="",
        context_prompt="",
        agents=[
            AgentConfig(name="Roots", role="bass", port="bass", channel=0, density=0.4),
            AgentConfig(name="Sticks", role="drums", port="drums", channel=9, density=0.4),
        ],
    )


@pytest.fixture
def demo_session() -> SessionConfig:
    return load_session(REPO / "config" / "sessions" / "demo.yaml")
