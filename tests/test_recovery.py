from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from alfred.ai.fake import FakeProvider
from alfred.automation.input_service import UserBusyError
from alfred.core.event_bus import EventBus
from alfred.core.input_lock import InputLockTimeoutError
from alfred.core.orchestrator import Orchestrator
from alfred.core.recovery import ErrorKind, classify
from alfred.core.task_tracker import TaskTracker
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import Risk, Tool, ToolError, ToolRegistry
from alfred.domain.ai import ChatMessage, ToolCall
from alfred.domain.events import ApplicationStarted, Event
from alfred.domain.tasks import StepStatus, TaskFinished, TasksInterrupted, TaskStatus
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.task_repository import SqlTaskRepository
from alfred.ui.tray import interrupted_message


def test_error_classification() -> None:
    timeout = ToolError("'digitar' excedeu 30 s")
    timeout.__cause__ = TimeoutError()
    cases: list[tuple[BaseException, bool, ErrorKind, bool]] = [
        (UserBusyError("usuário digitando"), False, ErrorKind.TRANSIENT, True),
        (InputLockTimeoutError("ocupado"), False, ErrorKind.TRANSIENT, True),
        (timeout, False, ErrorKind.TRANSIENT, False),  # pode ter digitado pela metade
        (timeout, True, ErrorKind.TRANSIENT, True),  # leitura: repetir é seguro
        (ConnectionError("rede"), True, ErrorKind.TRANSIENT, True),
        (LookupError("elemento 'Salvar' não encontrado"), False, ErrorKind.PLAN, False),
        (ValueError("monitor 9 inexistente"), False, ErrorKind.PLAN, False),
        (PermissionError("captura negada"), True, ErrorKind.DENIED, False),
        (RuntimeError("O Windows não permitiu o foco"), False, ErrorKind.ENVIRONMENT, False),
        (OSError("acesso negado pelo sistema"), False, ErrorKind.ENVIRONMENT, False),
    ]
    for exc, idempotent, kind, retry in cases:
        verdict = classify(exc, idempotent=idempotent)
        assert (verdict.kind, verdict.retry_safe) == (kind, retry), exc


class TextParams(BaseModel):
    texto: str = "x"


class Planner:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def chat(self, messages: list[ChatMessage], *, tools: Any = None) -> Any:
        return await self.provider.chat(messages, model="fake", tools=tools)


class Permissions:
    def __init__(self, deny: set[str] | None = None) -> None:
        self.deny = deny or set()

    async def authorize(self, tool: Tool, summary: str, target: str = "") -> bool:
        return tool.name not in self.deny


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


async def collect(bus: EventBus) -> list[Event]:
    seen: list[Event] = []

    async def record(event: Event) -> None:
        seen.append(event)

    bus.subscribe(Event, record)
    return seen


def call(name: str, call_id: str) -> ChatMessage:
    return ChatMessage(role="assistant", tool_calls=(ToolCall(id=call_id, name=name),))


def orchestrator(
    bus: EventBus, provider: FakeProvider, tools: ToolRegistry, **kwargs: Any
) -> Orchestrator:
    return Orchestrator(
        Planner(provider),  # type: ignore[arg-type]
        tools,
        kwargs.pop("permissions", Permissions()),  # type: ignore[arg-type]
        TaskRegistry(),
        bus,
        speak=False,
        backoff=0.001,
        **kwargs,
    )


async def test_transient_failure_is_retried_with_backoff(bus: EventBus) -> None:
    attempts: list[int] = []

    async def type_text(p: TextParams) -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) < 3:
            raise UserBusyError("o usuário está digitando")
        return {"digitado": 1}

    tools = ToolRegistry()
    tools.register(Tool("digitar_texto", "Digita", TextParams, type_text, "input"))
    provider = FakeProvider(
        replies=[call("digitar_texto", "1"), ChatMessage(role="assistant", content="Feito.")]
    )
    events = await collect(bus)
    reply = await orchestrator(bus, provider, tools).run("digite x")
    await bus.drain(timeout=1)
    assert reply == "Feito."
    assert len(attempts) == 3
    steps = [
        e for e in events if hasattr(e, "status") and e.__class__.__name__ == "TaskStepFinished"
    ]
    assert [s.status for s in steps] == [StepStatus.SUCCEEDED]  # uma etapa, recuperada


async def test_unsafe_transient_failure_is_not_repeated(bus: EventBus) -> None:
    attempts: list[int] = []

    async def type_text(p: TextParams) -> dict[str, Any]:
        attempts.append(1)
        raise TimeoutError  # pode ter digitado metade do texto

    tools = ToolRegistry()
    tools.register(Tool("digitar_texto", "Digita", TextParams, type_text, "input"))
    provider = FakeProvider(
        replies=[call("digitar_texto", "1"), ChatMessage(role="assistant", content="Falhou.")]
    )
    await orchestrator(bus, provider, tools).run("digite x")
    assert len(attempts) == 1
    result = json.loads(next(m for m in provider.calls[1]["messages"] if m.role == "tool").content)
    assert result["tipo_erro"] == "transitorio"
    assert result["replanejamentos_restantes"] == 2


async def test_replanning_is_limited(bus: EventBus) -> None:
    async def click(p: TextParams) -> dict[str, Any]:
        raise LookupError("elemento não encontrado")

    tools = ToolRegistry()
    tools.register(Tool("clicar_elemento", "Clica", TextParams, click, "ui"))
    provider = FakeProvider(replies=[call("clicar_elemento", str(i)) for i in range(10)])
    events = await collect(bus)
    reply = await orchestrator(bus, provider, tools, max_failures=3).run("clique em salvar")
    await bus.drain(timeout=1)
    assert "depois de 4 tentativas" in reply
    assert "elemento não encontrado" in reply
    assert len(provider.calls) == 4  # o modelo replanejou 3 vezes e então paramos
    remaining = [
        json.loads(m.content)["replanejamentos_restantes"]
        for m in provider.calls[3]["messages"]
        if m.role == "tool"
    ]
    assert remaining == [2, 1, 0]
    finished = next(e for e in events if isinstance(e, TaskFinished))
    assert finished.status is TaskStatus.FAILED
    assert finished.error == "limite de replanejamentos"


async def test_denials_do_not_consume_replanning(bus: EventBus) -> None:
    async def send(p: TextParams) -> dict[str, Any]:
        return {}

    tools = ToolRegistry()
    tools.register(Tool("enviar_email", "Envia", TextParams, send, "messages", Risk.SENSITIVE))
    provider = FakeProvider(
        replies=[call("enviar_email", str(i)) for i in range(5)]
        + [ChatMessage(role="assistant", content="Você não autorizou o envio.")]
    )
    reply = await orchestrator(
        bus, provider, tools, max_failures=1, permissions=Permissions(deny={"enviar_email"})
    ).run("envie o e-mail")
    assert reply == "Você não autorizou o envio."
    denied = json.loads(next(m for m in provider.calls[1]["messages"] if m.role == "tool").content)
    assert denied["tipo_erro"] == "negado"
    assert "não tente contornar" in denied["orientacao"]


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "alfred.db")
    await db.start()
    yield db
    await db.stop()


async def test_tasks_interrupted_by_a_crash_are_marked_and_announced(
    database: Database, bus: EventBus
) -> None:
    repo = SqlTaskRepository(database)
    now = datetime.now(UTC)
    crashed, done = uuid4(), uuid4()
    for task_id, title in ((crashed, "abra o relatório e envie"), (done, "que horas são")):
        await repo.task_started(
            task_id=task_id, correlation_id=uuid4(), title=title, origin="voice", at=now
        )
    await repo.step_started(task_id=crashed, position=1, tool="abrir", params={}, at=now)
    await repo.task_finished(task_id=done, status=TaskStatus.SUCCEEDED, error=None, at=now)
    # ... o processo caiu aqui; no reinício:
    events = await collect(bus)
    tracker = TaskTracker(bus, repo)
    await tracker.start()
    await bus.publish(ApplicationStarted())
    await bus.drain(timeout=1)
    tasks = {t.id: t for t in await repo.list_tasks()}
    assert tasks[crashed].status is TaskStatus.INTERRUPTED
    assert tasks[crashed].steps[0].status is StepStatus.CANCELLED
    assert tasks[done].status is TaskStatus.SUCCEEDED
    [notice] = [e for e in events if isinstance(e, TasksInterrupted)]
    assert notice.titles == ("abra o relatório e envie",)
    assert "Nada foi retomado automaticamente" in interrupted_message(notice)
    await tracker.stop()
