from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from alfred.ai.fake import FakeProvider
from alfred.core.event_bus import EventBus
from alfred.core.orchestrator import AssistantReply, Orchestrator
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import ToolRegistry
from alfred.domain.ai import ChatMessage
from alfred.domain.tasks import TaskStarted
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.memory_repository import SqlMemoryRepository
from alfred.memory.service import CONTEXT_CHAR_LIMIT, MemoryService


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


async def test_repository_crud(database: Database) -> None:
    repo = SqlMemoryRepository(database)
    first = await repo.add("fact", "Meu editor é o VS Code", "usuário")
    second = await repo.add("preference", "Prefiro respostas curtas")
    assert {m.content for m in await repo.list()} == {first.content, second.content}
    assert await repo.update(first.id, "Meu editor é o Neovim")
    assert any(m.content == "Meu editor é o Neovim" for m in await repo.list())
    assert await repo.delete(second.id)
    assert not await repo.delete(second.id)
    assert [m.id for m in await repo.list()] == [first.id]


async def test_context_respects_limit_and_deleted_items(database: Database, bus: EventBus) -> None:
    service = MemoryService(SqlMemoryRepository(database), bus)
    assert await service.context() == ""
    keep = await service.remember("Moro em Recife")
    gone = await service.remember("Trabalho com Python")
    assert "Trabalho com Python" in await service.context()
    await service.forget(gone.id)
    context = await service.context()
    assert "Moro em Recife" in context
    assert "Trabalho com Python" not in context
    await service.edit(keep.id, "Moro em Olinda")
    assert "Olinda" in await service.context()
    for i in range(100):
        await service.remember(f"fato número {i} " + "x" * 40)
    body = (await service.context()).split("\n", 1)[1]
    assert len(body) <= CONTEXT_CHAR_LIMIT + 100  # inclui as quebras de linha


async def test_conversations_are_recorded_from_events(database: Database, bus: EventBus) -> None:
    repo = SqlMemoryRepository(database)
    service = MemoryService(repo, bus)
    await service.start()
    task = uuid4()
    await bus.publish(TaskStarted(task_id=task, title="que horas são", origin="voice"))
    await bus.publish(TaskStarted(task_id=uuid4(), title="interno", origin="trigger"))
    await bus.publish(AssistantReply(task_id=task, text="São dez horas."))
    await bus.drain(timeout=1)
    conversations = await repo.conversations()
    assert len(conversations) == 1
    _, title, count = conversations[0]
    assert title == "que horas são"
    assert count == 2
    await service.stop()


async def test_forget_everything(database: Database, bus: EventBus) -> None:
    repo = SqlMemoryRepository(database)
    service = MemoryService(repo, bus)
    await service.remember("algo")
    await repo.add_message(None, "user", "olá")
    await service.forget_everything()
    assert await service.memories() == []
    assert await repo.conversations() == []
    assert await service.context() == ""


class Planner:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def chat(self, messages: list[ChatMessage], *, tools: object = None) -> object:
        return await self.provider.chat(messages, model="fake", tools=tools)  # type: ignore[arg-type]


class Allow:
    async def authorize(self, *args: object, **kwargs: object) -> bool:
        return True


async def test_orchestrator_includes_memories_in_system_prompt(
    database: Database, bus: EventBus
) -> None:
    service = MemoryService(SqlMemoryRepository(database), bus)
    await service.remember("Prefiro respostas curtas")
    provider = FakeProvider(replies=[ChatMessage(role="assistant", content="Certo.")])
    orch = Orchestrator(
        Planner(provider),  # type: ignore[arg-type]
        ToolRegistry(),
        Allow(),  # type: ignore[arg-type]
        TaskRegistry(),
        bus,
        context_provider=service.context,
    )
    await orch.run("oi")
    system = provider.calls[0]["messages"][0]
    assert system.role == "system"
    assert "Prefiro respostas curtas" in system.content
