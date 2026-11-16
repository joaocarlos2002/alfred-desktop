from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from alfred.ai.fake import FakeProvider
from alfred.core.commands import CommandRegistry
from alfred.core.event_bus import EventBus
from alfred.core.orchestrator import Orchestrator
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import Tool, ToolRegistry
from alfred.domain.ai import ChatMessage, ToolCall
from alfred.domain.events import Event
from alfred.domain.tasks import StepStatus, TaskFinished, TaskStatus, TaskStepFinished
from alfred.domain.triggers import TriggerCondition, TriggerFired, TriggerSkipped, TriggerSpec
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.trigger_repository import SqlTriggerRepository
from alfred.triggers.cron import Cron, CronError
from alfred.triggers.engine import PowerState, TriggerEngine

MONDAY_9 = datetime(2026, 11, 16, 9, 0)  # segunda-feira


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "alfred.db")
    await db.start()
    yield db
    await db.stop()


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


class Submitted:
    def __init__(self) -> None:
        self.requests: list[tuple[str, UUID]] = []

    def __call__(self, request: str, task_id: UUID) -> None:
        self.requests.append((request, task_id))

    @property
    def texts(self) -> list[str]:
        return [r for r, _ in self.requests]


class FakeHotkeys:
    def __init__(self, conflict: set[str] | None = None) -> None:
        self.callbacks: dict[int, tuple[str, Callable[[], None]]] = {}
        self.conflict = conflict or set()

    async def register_extra(self, hotkey: str, callback: Callable[[], None]) -> int:
        if hotkey in self.conflict:
            raise ValueError(f"atalho '{hotkey}' já está em uso")
        hotkey_id = len(self.callbacks) + 1
        self.callbacks[hotkey_id] = (hotkey, callback)
        return hotkey_id

    async def unregister_extra(self, hotkey_id: int) -> None:
        self.callbacks.pop(hotkey_id, None)


async def collect(bus: EventBus) -> list[Event]:
    seen: list[Event] = []

    async def record(event: Event) -> None:
        seen.append(event)

    bus.subscribe(Event, record)
    return seen


def engine(
    database: Database, bus: EventBus, submit: Any, **kwargs: Any
) -> tuple[TriggerEngine, SqlTriggerRepository]:
    repo = SqlTriggerRepository(database)
    kwargs.setdefault("processes", None)
    kwargs.setdefault("power", None)
    kwargs.setdefault("clock", lambda: MONDAY_9)
    return TriggerEngine(repo, bus, submit, **kwargs), repo


def spec(kind: str, request: str = "faça algo", **config: Any) -> TriggerSpec:
    return TriggerSpec(name=f"g-{kind}-{request}", kind=kind, config=config, request=request)  # type: ignore[arg-type]


# ---------------------------------------------------------------------- cron
def test_cron_parsing_and_matching() -> None:
    weekdays_8am = Cron.parse("0 8 * * 1-5")
    assert weekdays_8am.matches(datetime(2026, 11, 16, 8, 0))  # segunda
    assert not weekdays_8am.matches(datetime(2026, 11, 15, 8, 0))  # domingo
    assert not weekdays_8am.matches(datetime(2026, 11, 16, 8, 1))
    every_15 = Cron.parse("*/15 * * * *")
    assert every_15.matches(datetime(2026, 1, 1, 3, 45))
    assert Cron.parse("0 9 * * 0").matches(datetime(2026, 11, 15, 9, 0))  # 0 = domingo
    assert Cron.parse("0 9 * * 7").matches(datetime(2026, 11, 15, 9, 0))  # 7 = domingo
    assert weekdays_8am.next_after(datetime(2026, 11, 13, 9, 0)) == datetime(2026, 11, 16, 8, 0)
    assert Cron.parse("30 6 1 1 *").next_after(datetime(2026, 11, 16)) == datetime(
        2027, 1, 1, 6, 30
    )
    for bad in ("0 8 * *", "61 * * * *", "* * * * x", "*/0 * * * *"):
        with pytest.raises(CronError):
            Cron.parse(bad)


def test_condition_hours_and_weekdays() -> None:
    office = TriggerCondition(hours="08:00-18:00", weekdays=(0, 1, 2, 3, 4))
    assert office.allows(MONDAY_9)
    assert not office.allows(datetime(2026, 11, 16, 19, 0))
    assert not office.allows(datetime(2026, 11, 15, 9, 0))  # domingo
    night = TriggerCondition(hours="22:00-06:00")
    assert night.allows(datetime(2026, 11, 16, 23, 30))
    assert night.allows(datetime(2026, 11, 16, 5, 0))
    assert not night.allows(MONDAY_9)
    with pytest.raises(ValidationError):
        TriggerCondition(hours="8h-18h")


# ---------------------------------------------------------------------- fontes
async def test_voice_phrase_source(database: Database, bus: EventBus) -> None:
    commands = CommandRegistry()
    submitted = Submitted()
    eng, _ = engine(database, bus, submitted, commands=commands)
    await eng.start()
    await eng.save(spec("voice", "abra o outlook e o teams", frase="Modo Trabalho"))
    found = commands.match("modo trabalho")
    assert found is not None
    match, handler = found
    result = await handler(match)
    assert result.succeeded
    assert submitted.texts == ["abra o outlook e o teams"]
    await eng.stop()


async def test_hotkey_source_fires_from_backend_thread(database: Database, bus: EventBus) -> None:
    hotkeys = FakeHotkeys(conflict={"ctrl+alt+x"})
    submitted = Submitted()
    eng, _ = engine(database, bus, submitted, hotkeys=hotkeys)
    await eng.start()
    await eng.save(spec("hotkey", "tire um print", atalho="ctrl+alt+p"))
    broken = await eng.save(spec("hotkey", "outro", atalho="ctrl+alt+x"))
    [(combo, callback)] = hotkeys.callbacks.values()
    assert combo == "ctrl+alt+p"
    thread = threading.Thread(target=callback)  # como o listener Win32 faria
    thread.start()
    thread.join()
    for _ in range(50):
        if submitted.texts:
            break
        await asyncio.sleep(0.01)
    assert submitted.texts == ["tire um print"]
    assert broken.id in eng.problems  # conflito reportado, não silencioso
    await eng.stop()
    assert hotkeys.callbacks == {}  # desarmado ao parar


async def test_schedule_source(database: Database, bus: EventBus) -> None:
    submitted = Submitted()
    eng, _ = engine(database, bus, submitted)
    await eng.start()
    await eng.save(spec("schedule", "leia minha agenda", cron="0 9 * * 1-5"))
    await eng.check_schedule(datetime(2026, 11, 16, 8, 59))
    assert submitted.texts == []
    await eng.check_schedule(MONDAY_9)
    assert submitted.texts == ["leia minha agenda"]
    with pytest.raises(ValueError, match="5 campos"):
        await eng.save(spec("schedule", "x", cron="todo dia"))
    await eng.stop()


async def test_startup_source(database: Database, bus: EventBus) -> None:
    repo = SqlTriggerRepository(database)
    await repo.save(spec("startup", "bom dia, abra o e-mail"))
    submitted = Submitted()
    eng = TriggerEngine(repo, bus, submitted, processes=None, power=None)
    await eng.start()
    assert submitted.texts == ["bom dia, abra o e-mail"]
    await eng.stop()


async def test_app_open_source(database: Database, bus: EventBus) -> None:
    snapshots = iter([{"explorer.exe"}, {"explorer.exe", "winword.exe"}, {"winword.exe"}])
    submitted = Submitted()
    eng, _ = engine(database, bus, submitted, processes=lambda: next(snapshots))
    await eng.start()
    await eng.save(spec("app_open", "abra meu modelo de relatório", app="WINWORD"))
    await eng.poll()  # referência: nada dispara para o que já estava aberto
    await eng.poll()  # Word abriu
    await eng.poll()  # continua aberto: não dispara de novo
    assert submitted.texts == ["abra meu modelo de relatório"]
    await eng.stop()


async def test_system_power_events(database: Database, bus: EventBus) -> None:
    states = iter(
        [
            PowerState(plugged=True, percent=80),
            PowerState(plugged=False, percent=80),
            PowerState(plugged=False, percent=19),
            PowerState(plugged=True, percent=19),
        ]
    )
    submitted = Submitted()
    eng, _ = engine(database, bus, submitted, power=lambda: next(states))
    await eng.start()
    for event in ("energia_conectada", "energia_desconectada", "bateria_baixa"):
        await eng.save(spec("system", event, evento=event))
    with pytest.raises(ValueError, match="desconhecido"):
        await eng.save(spec("system", "x", evento="terremoto"))
    for _ in range(4):
        await eng.poll()
    assert submitted.texts == ["energia_desconectada", "bateria_baixa", "energia_conectada"]
    await eng.stop()


# ---------------------------------------------------------------------- disparo e registro
async def test_condition_disabled_and_overlap_are_skipped(
    database: Database, bus: EventBus
) -> None:
    events = await collect(bus)
    submitted = Submitted()
    eng, _ = engine(database, bus, submitted, clock=lambda: datetime(2026, 11, 16, 20, 0))
    await eng.start()
    office = await eng.save(
        spec("schedule", "x", cron="* * * * *").model_copy(
            update={"condition": TriggerCondition(hours="08:00-18:00")}
        )
    )
    assert not await eng.fire(office)
    always = await eng.save(spec("voice", "y", frase="teste"))
    assert await eng.fire(always)
    assert not await eng.fire(always)  # execução anterior ainda rodando
    disabled = await eng.set_enabled(always.id or 0, False)
    assert not await eng.fire(disabled)
    await bus.drain(timeout=1)
    reasons = [e.reason for e in events if isinstance(e, TriggerSkipped)]
    assert reasons == [
        "fora da condição de horário/dia",
        "execução anterior ainda em andamento",
        "gatilho desabilitado",
    ]
    assert len([e for e in events if isinstance(e, TriggerFired)]) == 1
    await eng.stop()


async def test_executions_are_recorded_and_interrupted_on_restart(
    database: Database, bus: EventBus
) -> None:
    submitted = Submitted()
    eng, repo = engine(database, bus, submitted)
    await eng.start()
    done = await eng.save(spec("voice", "a", frase="um"))
    pending = await eng.save(spec("voice", "b", frase="dois"))
    await eng.fire(done)
    await eng.fire(pending)
    task_id = submitted.requests[0][1]
    await bus.publish(TaskFinished(task_id=task_id, status=TaskStatus.SUCCEEDED))
    await bus.drain(timeout=1)
    await eng.stop()
    # "reinício": a execução que ficou em andamento é marcada como interrompida
    eng2 = TriggerEngine(repo, bus, Submitted(), processes=None, power=None)
    await eng2.start()
    by_trigger = {r.trigger_id: r for r in await eng2.executions()}
    assert by_trigger[done.id or 0].status == "succeeded"
    assert by_trigger[pending.id or 0].status == "interrupted"
    await eng2.stop()


class AppParams(BaseModel):
    nome: str


class Planner:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def chat(self, messages: list[ChatMessage], *, tools: Any = None) -> Any:
        return await self.provider.chat(messages, model="fake", tools=tools)


class Deny:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def authorize(self, tool: Tool, summary: str, target: str = "") -> bool:
        self.asked.append(tool.name)
        return False


async def test_trigger_runs_through_orchestrator_and_permissions(
    database: Database, bus: EventBus
) -> None:
    opened: list[str] = []

    async def open_app(p: AppParams) -> dict[str, Any]:
        opened.append(p.nome)
        return {}

    tools = ToolRegistry()
    tools.register(Tool("abrir_aplicativo", "Abre", AppParams, open_app, "applications"))
    provider = FakeProvider(
        replies=[
            ChatMessage(
                role="assistant",
                tool_calls=(ToolCall(id="1", name="abrir_aplicativo", arguments={"nome": "x"}),),
            ),
            ChatMessage(role="assistant", content="Não tive permissão."),
        ]
    )
    permissions = Deny()
    tasks = TaskRegistry()
    orch = Orchestrator(Planner(provider), tools, permissions, tasks, bus, speak=False)  # type: ignore[arg-type]
    events = await collect(bus)
    eng, _ = engine(
        database,
        bus,
        lambda request, task_id: orch.submit(request, origin="trigger", task_id=task_id),
    )
    await eng.start()
    trigger = await eng.save(spec("voice", "abra x", frase="abrir x"))
    await eng.fire(trigger)
    for _ in range(100):
        if any(isinstance(e, TaskFinished) for e in events):
            break
        await asyncio.sleep(0.01)
    await bus.drain(timeout=1)
    assert opened == []  # a permissão negou: nada executado
    assert permissions.asked == ["abrir_aplicativo"]
    assert [e.status for e in events if isinstance(e, TaskStepFinished)] == [StepStatus.DENIED]
    [record] = await eng.executions(trigger.id)
    assert record.status == "succeeded"  # a tarefa terminou (com a recusa explicada)
    await eng.stop()
