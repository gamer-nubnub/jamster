"""MIDI port helpers over mido/python-rtmidi.

Ports are matched by case-insensitive substring so configs can say
``jamster-bass`` and match ``jamster-bass 2`` (loopMIDI numbering).
"""

from __future__ import annotations

import mido


def list_output_ports() -> list[str]:
    return list(mido.get_output_names())


def list_input_ports() -> list[str]:
    return list(mido.get_input_names())


def _match(fragment: str, names: list[str], kind: str) -> str:
    matches = [name for name in names if fragment.lower() in name.lower()]
    if not matches:
        available = ", ".join(names) or "(none)"
        raise LookupError(
            f"No {kind} MIDI port matching '{fragment}'. Available: {available}. "
            "Create ports with loopMIDI (see README)."
        )
    return matches[0]


def open_output(name_fragment: str) -> mido.ports.BaseOutput:
    return mido.open_output(_match(name_fragment, list_output_ports(), "output"))


def open_input(name_fragment: str) -> mido.ports.BaseInput:
    return mido.open_input(_match(name_fragment, list_input_ports(), "input"))
