"""LLM backends for the producer (Tier 2) and performer (Tier 1).

``LLMBackend`` is the only surface the rest of jamster depends on. The real
implementation wraps the GitHub Copilot SDK (uses your Copilot subscription;
``pip install jamster[copilot]``). ``FakeBackend`` serves scripted responses
so the full pipeline is testable offline.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    latency_seconds: float
    cost: float | None = None  # as reported by the backend, if at all

    def json(self) -> Any:
        text = self.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        return json.loads(text)


@dataclass
class Spend:
    """Running cost tally; feeds the session budget cap."""

    total_cost: float = 0.0
    calls: int = 0
    unknown_cost_calls: int = 0

    def add(self, completion: Completion) -> None:
        self.calls += 1
        if completion.cost is None:
            self.unknown_cost_calls += 1
        else:
            self.total_cost += completion.cost


class LLMBackend(Protocol):
    spend: Spend

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def list_models(self) -> list[str]: ...
    async def complete(
        self,
        *,
        role: str,
        system: str,
        prompt: str,
        model: str,
        schema: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> Completion: ...


# --------------------------------------------------------------------------- fake

Responder = Callable[[str, str], str | Awaitable[str]]  # (role, prompt) -> text


@dataclass
class FakeCall:
    role: str
    model: str
    system: str
    prompt: str


class FakeBackend:
    """Scripted backend. Give it a responder or per-role queues of texts.

    ``delay`` simulates latency so deadline/fallback paths get exercised.
    """

    def __init__(
        self,
        responder: Responder | None = None,
        queues: dict[str, list[str]] | None = None,
        delay: float = 0.0,
        models: tuple[str, ...] = ("fake-fast", "fake-smart"),
    ) -> None:
        self.responder = responder
        self.queues = {k: list(v) for k, v in (queues or {}).items()}
        self.delay = delay
        self.models = list(models)
        self.calls: list[FakeCall] = []
        self.spend = Spend()
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def list_models(self) -> list[str]:
        return list(self.models)

    async def complete(
        self,
        *,
        role: str,
        system: str,
        prompt: str,
        model: str,
        schema: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> Completion:
        self.calls.append(FakeCall(role, model, system, prompt))
        started = time.perf_counter()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.responder is not None:
            result = self.responder(role, prompt)
            text = await result if asyncio.iscoroutine(result) else result
        elif self.queues.get(role):
            text = self.queues[role].pop(0)
        else:
            raise RuntimeError(f"FakeBackend has no response for role {role!r}")
        completion = Completion(text=str(text), model=model, latency_seconds=time.perf_counter() - started, cost=0.0)
        self.spend.add(completion)
        return completion


# ------------------------------------------------------------------ copilot sdk


class CopilotBackend:
    """GitHub Copilot SDK backend: one persistent chat session per (role, model).

    Sessions run with no tools and a replaced system prompt, so each call is a
    plain prompt → text exchange. Cost is taken from the SDK's usage events
    when the runtime reports it.
    """

    def __init__(self, log_level: str = "error") -> None:
        try:
            from copilot import CopilotClient
        except ImportError as error:  # pragma: no cover - depends on extras
            raise RuntimeError(
                "Copilot backend requested but github-copilot-sdk is not installed; "
                "run: pip install jamster[copilot]"
            ) from error
        self._client = CopilotClient(log_level=log_level)  # type: ignore[arg-type]
        self._sessions: dict[tuple[str, str], Any] = {}
        self._pending_cost: dict[str, float] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.spend = Spend()

    async def start(self) -> None:
        await self._client.start()

    async def stop(self) -> None:
        for session in self._sessions.values():
            try:
                await session.disconnect()
            except Exception:  # pragma: no cover - best effort shutdown
                pass
        self._sessions.clear()
        await self._client.stop()

    async def list_models(self) -> list[str]:
        return [m.id for m in await self._client.list_models()]

    async def _session(self, role: str, model: str, system: str) -> Any:
        key = (role, model)
        if key not in self._sessions:
            session = await self._client.create_session(
                model=model,
                name=f"jamster-{role}",
                available_tools=[],
                skip_custom_instructions=True,
                system_message={"mode": "replace", "content": system},
                enable_session_store=False,
            )
            session.on(lambda event, key=key: self._on_event(key, event))
            self._sessions[key] = session
            self._locks[key] = asyncio.Lock()
        return self._sessions[key]

    def _on_event(self, key: tuple[str, str], event: Any) -> None:
        data = getattr(event, "data", None)
        cost = getattr(data, "cost", None)
        if type(data).__name__ == "AssistantUsageData" and cost is not None:
            self._pending_cost[key[0]] = self._pending_cost.get(key[0], 0.0) + float(cost)

    async def complete(
        self,
        *,
        role: str,
        system: str,
        prompt: str,
        model: str,
        schema: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> Completion:
        session = await self._session(role, model, system)
        started = time.perf_counter()
        async with self._locks[(role, model)]:
            self._pending_cost.pop(role, None)
            event = await session.send_and_wait(prompt, response_schema=schema, timeout=timeout)
        text = getattr(getattr(event, "data", None), "content", "") if event is not None else ""
        completion = Completion(
            text=text,
            model=model,
            latency_seconds=time.perf_counter() - started,
            cost=self._pending_cost.pop(role, None),
        )
        self.spend.add(completion)
        return completion


def make_backend(kind: str) -> LLMBackend | None:
    """``copilot`` → CopilotBackend, ``none`` → None (deterministic band only)."""
    if kind == "none":
        return None
    if kind == "copilot":
        return CopilotBackend()
    raise ValueError(f"Unknown LLM backend {kind!r} (expected 'copilot' or 'none')")
