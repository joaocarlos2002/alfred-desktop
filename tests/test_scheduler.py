from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from alfred.ai.fake import FakeProvider
from alfred.core.event_bus import EventBus
from alfred.core.input_lock import InputLock
from alfred.core.orchestrator import Orchestrator
from alfred.core.scheduler import INPUT, Step, StepScheduler, plan_batch
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import Risk, Tool, ToolRegistry
from alfred.domain.ai import ChatMessage, ToolCall


class Probe:
    """Mede concorrência: quantas etapas estão ativas ao mesmo tempo, por recurso."""

    def __init__(self) -> None:
        self.active: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        self.order: list[str] = []

    async def work(self, label: str, resource: str, delay: float = 0.01) -> str:
        self.active[resource] = self.active.get(resource, 0) + 1
        self.peak[resource] = max(self.peak.get(resource, 0), self.active[resource])
        try:
            await asyncio.sleep(delay)
            self.order.append(label)
            return label
        finally:
            self.active[resource] -= 1


def test_plan_batch_dependencies() -> None:
    steps = plan_batch(
        [
            Step(1, "abrir A", INPUT),
            Step(2, "baixar", None),
            Step(3, "abrir B", INPUT),
            Step(4, "pagina", "browser"),
            Step(5, "ler", None),
        ]
    )
    deps = {s.key: s.depends_on for s in steps}
    assert deps == {1: set(), 2: {1}, 3: {1}, 4: set(), 5: {1, 3, 4}}


async def test_independent_steps_run_in_parallel_up_to_the_limit() -> None:
    probe = Probe()
    scheduler = StepScheduler(max_parallel=lambda: 3)
    steps = [Step(i, f"rede{i}") for i in range(10)]
    results = await scheduler.run(steps, lambda s: probe.work(s.name, "free", 0.05))
    assert probe.peak["free"] == 3
    assert sorted(results) == list(range(10))


async def test_input_steps_are_serialized_in_order_and_hold_the_input_lock() -> None:
    lock = InputLock()
    probe = Probe()
    scheduler = StepScheduler(lock, max_parallel=lambda: 8)

    async def runner(step: Step) -> str:
        assert lock.locked
        async with lock.hold("reentrada"):  # ex.: InputService dentro da etapa — sem deadlock
            return await probe.work(step.name, INPUT)

    steps = plan_batch([Step(i, f"tecla{i}", INPUT) for i in range(6)])
    await scheduler.run(steps, runner)
    assert probe.peak[INPUT] == 1
    assert probe.order == [f"tecla{i}" for i in range(6)]
    assert not lock.locked


async def test_stress_concurrent_tasks_never_overlap_on_input() -> None:
    """Vários pedidos simultâneos, cada um com etapas de input, rede e navegador misturadas."""
    lock = InputLock()
    probe = Probe()
    scheduler = StepScheduler(lock, max_parallel=lambda: 6)
    rng = random.Random(40)

    async def task(n: int) -> list[str]:
        kinds = [rng.choice([INPUT, "browser", None]) for _ in range(12)]
        steps = plan_batch([Step(i, f"t{n}-{i}", kind) for i, kind in enumerate(kinds)])

        async def runner(step: Step) -> str:
            if step.resource == INPUT:
                async with lock.hold(step.name):
                    return await probe.work(step.name, INPUT, rng.random() / 200)
            return await probe.work(step.name, step.resource or "free", rng.random() / 200)

        await scheduler.run(steps, runner)
        return [s.name for s in steps if s.resource == INPUT]

    expected = await asyncio.gather(*(task(n) for n in range(8)))
    assert probe.peak[INPUT] == 1
    assert probe.peak.get("browser", 1) == 1
    for inputs in expected:  # dentro de cada pedido, o input segue a ordem proposta
        positions = [probe.order.index(name) for name in inputs]
        assert positions == sorted(positions)


async def test_invalid_graphs_are_rejected() -> None:
    scheduler = StepScheduler()
    with pytest.raises(ValueError, match="Ciclo"):
        await scheduler.run([Step(1, "a", depends_on={2}), Step(2, "b", depends_on={1})], _noop)
    with pytest.raises(ValueError, match="inexistentes"):
        await scheduler.run([Step(1, "a", depends_on={9})], _noop)


async def test_cancelling_the_batch_releases_the_input_lock() -> None:
    lock = InputLock()
    scheduler = StepScheduler(lock)
    started = asyncio.Event()

    async def slow(step: Step) -> None:
        started.set()
        await asyncio.sleep(10)

    run = asyncio.create_task(scheduler.run(plan_batch([Step(1, "x", INPUT)]), slow))
    await started.wait()
    assert lock.locked
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert not lock.locked


async def _noop(step: Step) -> None:
    return None


class AppParams(BaseModel):
    nome: str


class UrlParams(BaseModel):
    url: str


class Planner:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def chat(self, messages: list[ChatMessage], *, tools: Any = None) -> Any:
        return await self.provider.chat(messages, model="fake", tools=tools)


class Allow:
    async def authorize(self, *args: object, **kwargs: object) -> bool:
        return True


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


async def test_orchestrator_runs_multi_app_batch_safely(bus: EventBus) -> None:
    lock = InputLock()
    probe = Probe()

    async def open_app(p: AppParams) -> dict[str, Any]:
        assert lock.locked
        return {"app": await probe.work(f"abrir:{p.nome}", INPUT)}

    async def fetch(p: UrlParams) -> dict[str, Any]:
        return {"url": await probe.work(f"baixar:{p.url}", "free", 0.03)}

    tools = ToolRegistry()
    tools.register(Tool("abrir_aplicativo", "Abre", AppParams, open_app, "apps", resource=INPUT))
    tools.register(Tool("baixar", "Baixa", UrlParams, fetch, "net", Risk.READ))
    calls = (
        ToolCall(id="1", name="baixar", arguments={"url": "a"}),
        ToolCall(id="2", name="baixar", arguments={"url": "b"}),
        ToolCall(id="3", name="abrir_aplicativo", arguments={"nome": "Calculadora"}),
        ToolCall(id="4", name="abrir_aplicativo", arguments={"nome": "Paint"}),
    )
    provider = FakeProvider(
        replies=[
            ChatMessage(role="assistant", tool_calls=calls),
            ChatMessage(role="assistant", content="Pronto."),
        ]
    )
    orch = Orchestrator(
        Planner(provider),  # type: ignore[arg-type]
        tools,
        Allow(),  # type: ignore[arg-type]
        TaskRegistry(),
        bus,
        scheduler=StepScheduler(lock, max_parallel=lambda: 4),
    )
    assert await orch.run("baixe a e b e abra a calculadora e o paint") == "Pronto."
    assert probe.peak["free"] == 2  # downloads em paralelo
    assert probe.peak[INPUT] == 1
    assert probe.order.index("abrir:Calculadora") < probe.order.index("abrir:Paint")
    tool_messages = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["1", "2", "3", "4"]
