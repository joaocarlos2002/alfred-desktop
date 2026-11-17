"""Memória de conversa (#118): o ALFRED lembra das últimas perguntas."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from alfred.ai.fake import FakeProvider
from alfred.config.settings import AISettings
from alfred.core.event_bus import EventBus
from alfred.core.orchestrator import AssistantReply, Orchestrator
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import ToolRegistry
from alfred.domain.ai import ChatMessage
from alfred.domain.tasks import TaskStarted
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.memory_repository import SqlMemoryRepository
from alfred.memory.service import MemoryService


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


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


def service(database: Database, bus: EventBus, clock: Clock, **ai: Any) -> MemoryService:
    settings = AISettings(**ai)
    return MemoryService(SqlMemoryRepository(database), bus, config=lambda: settings, clock=clock)


async def exchange(bus: EventBus, request: str, reply: str, origin: str = "voice") -> None:
    task = uuid4()
    await bus.publish(TaskStarted(task_id=task, title=request, origin=origin))
    await bus.publish(AssistantReply(task_id=task, text=reply))
    await bus.drain(timeout=1)


async def test_history_returns_previous_turns_in_order(database: Database, bus: EventBus) -> None:
    memory = service(database, bus, Clock())
    await memory.start()
    await exchange(bus, "abra o bloco de notas", "Abri o Bloco de notas.")
    await exchange(bus, "e a calculadora?", "Abri a Calculadora.")
    history = await memory.history()
    assert [(m.role, m.content) for m in history] == [
        ("user", "abra o bloco de notas"),
        ("assistant", "Abri o Bloco de notas."),
        ("user", "e a calculadora?"),
        ("assistant", "Abri a Calculadora."),
    ]
    await memory.stop()


async def test_idle_gap_starts_a_new_conversation(database: Database, bus: EventBus) -> None:
    clock = Clock()
    memory = service(database, bus, clock, conversation_idle_minutes=30)
    await memory.start()
    await exchange(bus, "qual é a capital da França?", "Paris.")
    clock.now += timedelta(minutes=31)
    assert await memory.history() == []  # conversa antiga não volta sozinha
    await exchange(bus, "e da Itália?", "Roma.")
    assert [m.content for m in await memory.history()] == ["e da Itália?", "Roma."]
    await memory.stop()


async def test_old_turns_become_a_summary_within_the_budget(
    database: Database, bus: EventBus
) -> None:
    memory = service(database, bus, Clock(), conversation_turns=2, conversation_char_budget=10_000)
    await memory.start()
    for i in range(5):
        await exchange(bus, f"pergunta {i}", f"resposta {i}")
    history = await memory.history()
    assert history[0].role == "system"
    assert "pergunta 0" in history[0].content
    assert "pergunta 2" in history[0].content
    assert [m.content for m in history[1:]] == [
        "pergunta 3",
        "resposta 3",
        "pergunta 4",
        "resposta 4",
    ]

    tight = service(database, bus, Clock(), conversation_turns=10, conversation_char_budget=25)
    tight._conversation = memory._conversation
    tight._last_at = memory._last_at
    kept = [m for m in await tight.history() if m.role != "system"]
    assert sum(len(m.content) for m in kept) <= 25
    assert kept[-1].content == "resposta 4"  # as mais recentes têm prioridade
    await memory.stop()


async def test_new_conversation_and_disable(database: Database, bus: EventBus) -> None:
    memory = service(database, bus, Clock())
    await memory.start()
    await exchange(bus, "lembra disso", "Ok.")
    await memory.new_conversation()
    assert await memory.history() == []
    off = service(database, bus, Clock(), conversation_turns=0)
    off._conversation = memory._conversation
    await exchange(bus, "algo", "certo")
    assert await off.history() == []
    await memory.stop()


async def test_conversation_survives_restart_within_idle_window(
    database: Database, bus: EventBus
) -> None:
    clock = Clock()
    first = service(database, bus, clock)
    await first.start()
    await exchange(bus, "meu time é o Sport", "Anotado.")
    await first.stop()
    clock.now += timedelta(minutes=5)
    again = service(database, bus, clock)
    await again.start()
    assert [m.content for m in await again.history()][-1] == "Anotado."
    await again.stop()
    clock.now += timedelta(hours=2)
    later = service(database, bus, clock)
    await later.start()
    assert await later.history() == []
    await later.stop()


class Planner:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def chat(self, messages: list[ChatMessage], *, tools: Any = None) -> Any:
        return await self.provider.chat(messages, model="fake", tools=tools)


class Allow:
    async def authorize(self, *args: object, **kwargs: object) -> bool:
        return True


async def test_follow_up_question_reaches_the_model_with_previous_turns(
    database: Database, bus: EventBus
) -> None:
    memory = service(database, bus, Clock())
    await memory.start()
    provider = FakeProvider(
        replies=[
            ChatMessage(role="assistant", content="A capital da França é Paris."),
            ChatMessage(role="assistant", content="A da Itália é Roma."),
            ChatMessage(role="assistant", content="Gatilho ok."),
        ]
    )
    orch = Orchestrator(
        Planner(provider),  # type: ignore[arg-type]
        ToolRegistry(),
        Allow(),  # type: ignore[arg-type]
        TaskRegistry(),
        bus,
        speak=False,
        history_provider=memory.history,
    )
    await orch.run("qual a capital da França?", origin="voice")
    await bus.drain(timeout=1)
    await orch.run("e da Itália?", origin="voice")
    await bus.drain(timeout=1)
    sent = provider.calls[1]["messages"]
    assert [m.content for m in sent if m.role in ("user", "assistant")] == [
        "qual a capital da França?",
        "A capital da França é Paris.",
        "e da Itália?",
    ]
    await orch.run("tarefa automática", origin="trigger")  # automações não herdam a conversa
    assert [m.role for m in provider.calls[2]["messages"]] == ["system", "user"]
    await memory.stop()
