from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel, Field

from alfred.ai.fake import FakeProvider
from alfred.core.event_bus import EventBus
from alfred.core.orchestrator import AssistantReply, Orchestrator
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import Tool, ToolRegistry
from alfred.domain.ai import ChatMessage, ProviderUnavailableError, ToolCall
from alfred.domain.events import Event
from alfred.domain.tasks import (
    StepStatus,
    TaskFinished,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)


class AppParams(BaseModel):
    nome: str = Field(min_length=1)


class TextParams(BaseModel):
    texto: str


class Planner:
    """Usa o FakeProvider como o AIService faria (mesma interface ``chat``)."""

    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def chat(self, messages: list[ChatMessage], *, tools: Any = None) -> Any:
        return await self.provider.chat(messages, model="fake", tools=tools)


class Permissions:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.asked: list[str] = []

    async def authorize(
        self, tool: Tool, summary: str, target: str = "", *, escalate: str | None = None
    ) -> bool:
        self.asked.append(tool.name)
        return self.allow


def tools(desktop: list[str], *, slow: bool = False) -> ToolRegistry:
    async def open_app(p: AppParams) -> dict[str, Any]:
        desktop.append(f"aberto:{p.nome}")
        return {"app": p.nome}

    async def type_text(p: TextParams) -> dict[str, Any]:
        if slow:
            await asyncio.sleep(10)
        desktop.append(f"digitado:{p.texto}")
        return {"digitado": len(p.texto)}

    registry = ToolRegistry()
    registry.register(Tool("abrir_aplicativo", "Abre um app", AppParams, open_app, "applications"))
    registry.register(Tool("digitar_texto", "Digita texto", TextParams, type_text, "input"))
    return registry


def assistant(*calls: ToolCall, text: str = "") -> ChatMessage:
    return ChatMessage(role="assistant", content=text, tool_calls=calls)


def call(name: str, call_id: str, **args: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args)


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


def orchestrator(
    bus: EventBus,
    provider: FakeProvider,
    registry: ToolRegistry,
    permissions: Permissions,
    **kwargs: Any,
) -> Orchestrator:
    return Orchestrator(
        Planner(provider),
        registry,
        permissions,  # type: ignore[arg-type]
        kwargs.pop("tasks", TaskRegistry()),
        bus,
        **kwargs,
    )


async def test_end_to_end_open_notepad_and_type_hello(bus: EventBus) -> None:
    desktop: list[str] = []
    provider = FakeProvider(
        replies=[
            assistant(call("abrir_aplicativo", "1", nome="Bloco de notas")),
            assistant(call("digitar_texto", "2", texto="olá")),
            assistant(text="Pronto: abri o Bloco de notas e escrevi olá."),
        ]
    )
    events = await collect(bus)

    reply = await orchestrator(bus, provider, tools(desktop), Permissions()).run(
        "abra o bloco de notas e escreva olá"
    )
    await bus.drain(timeout=1)

    assert desktop == ["aberto:Bloco de notas", "digitado:olá"]
    assert reply == "Pronto: abri o Bloco de notas e escrevi olá."
    steps = [e for e in events if isinstance(e, TaskStepFinished)]
    assert [s.status for s in steps] == [StepStatus.SUCCEEDED, StepStatus.SUCCEEDED]
    finished = next(e for e in events if isinstance(e, TaskFinished))
    assert finished.status is TaskStatus.SUCCEEDED
    assert any(isinstance(e, AssistantReply) for e in events)
    tool_messages = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert '"ok": true' in tool_messages[0].content  # resultado real devolvido ao modelo


async def test_invalid_arguments_are_returned_to_model_for_correction(bus: EventBus) -> None:
    desktop: list[str] = []
    provider = FakeProvider(
        replies=[
            assistant(call("abrir_aplicativo", "1", nome="")),
            assistant(call("abrir_aplicativo", "2", nome="Calculadora")),
            assistant(text="Calculadora aberta."),
        ]
    )
    events = await collect(bus)
    reply = await orchestrator(bus, provider, tools(desktop), Permissions()).run(
        "abra a calculadora"
    )
    await bus.drain(timeout=1)
    assert desktop == ["aberto:Calculadora"]
    assert reply == "Calculadora aberta."
    first = next(e for e in events if isinstance(e, TaskStepFinished))
    assert first.status is StepStatus.FAILED
    assert "Argumentos inválidos" in (first.error or "")


async def test_unknown_tool_and_denied_permission_never_execute(bus: EventBus) -> None:
    desktop: list[str] = []
    provider = FakeProvider(
        replies=[
            assistant(call("formatar_disco", "1"), call("abrir_aplicativo", "2", nome="x")),
            assistant(text="Não tive permissão para isso."),
        ]
    )
    events = await collect(bus)
    permissions = Permissions(allow=False)
    await orchestrator(bus, provider, tools(desktop), permissions).run("formate o disco")
    await bus.drain(timeout=1)
    assert desktop == []
    statuses = [e.status for e in events if isinstance(e, TaskStepFinished)]
    assert statuses == [StepStatus.FAILED, StepStatus.DENIED]
    assert permissions.asked == ["abrir_aplicativo"]


async def test_iteration_limit(bus: EventBus) -> None:
    desktop: list[str] = []
    loop = [assistant(call("abrir_aplicativo", str(i), nome="x")) for i in range(20)]
    events = await collect(bus)
    orch = orchestrator(
        bus, FakeProvider(replies=loop), tools(desktop), Permissions(), max_iterations=3
    )
    reply = await orch.run("loop")
    await bus.drain(timeout=1)
    assert "passos demais" in reply
    assert len(desktop) == 3
    assert next(e for e in events if isinstance(e, TaskFinished)).status is TaskStatus.FAILED


async def test_cancellation_stops_at_current_step(bus: EventBus) -> None:
    desktop: list[str] = []
    provider = FakeProvider(replies=[assistant(call("digitar_texto", "1", texto="longo"))])
    registry = TaskRegistry()
    orch = orchestrator(bus, provider, tools(desktop, slow=True), Permissions(), tasks=registry)
    events = await collect(bus)
    task = orch.submit("digite um texto longo")
    while not any(isinstance(e, TaskStepStarted) for e in events):
        await asyncio.sleep(0.01)
    await registry.cancel_all()  # ex.: interrupção de emergência ou botão "Cancelar"
    with pytest.raises(asyncio.CancelledError):
        await task
    await bus.drain(timeout=1)
    assert desktop == []
    finished = next(e for e in events if isinstance(e, TaskFinished))
    assert finished.status is TaskStatus.CANCELLED


async def test_provider_failure_is_reported_honestly(bus: EventBus) -> None:
    provider = FakeProvider(replies=[ProviderUnavailableError("Ollama indisponível")])
    events = await collect(bus)
    reply = await orchestrator(bus, provider, tools([]), Permissions()).run("olá")
    await bus.drain(timeout=1)
    assert "Ollama indisponível" in reply
    assert next(e for e in events if isinstance(e, TaskFinished)).status is TaskStatus.FAILED


async def test_free_voice_requests_go_to_orchestrator(bus: EventBus) -> None:
    from alfred.core.voice_commands import VoiceCommandHandler
    from alfred.domain.voice import UtteranceTranscribed

    sent: list[str] = []
    handler = VoiceCommandHandler(bus, emergency=_noop, fallback=sent.append)
    for text in ("Alfred, que horas são?", "Alfred, resuma meus e-mails"):
        await handler.handle(
            UtteranceTranscribed(
                text=text, audio_seconds=1, latency_seconds=0.1, trigger="wake_word"
            )
        )
    assert sent == ["Alfred, resuma meus e-mails"]  # hora continua no comando embutido


async def _noop() -> None:
    return None


async def test_system_prompt_lists_only_real_capabilities(bus: EventBus) -> None:
    """Regressão: sem a lista, o modelo dizia saber "organizar a agenda" e "mexer em arquivos"."""
    provider = FakeProvider(replies=[assistant(text="ok"), assistant(text="ok")])
    await orchestrator(bus, provider, tools([]), Permissions()).run("o que você sabe fazer?")
    system = provider.calls[0]["messages"][0].content
    assert "- Abre um app" in system
    assert "- Digita texto" in system
    assert "SOMENTE estas capacidades" in system

    empty = FakeProvider(replies=[assistant(text="ok")])
    await orchestrator(bus, empty, ToolRegistry(), Permissions()).run("o que você sabe fazer?")
    assert "só pode conversar" in empty.calls[0]["messages"][0].content
