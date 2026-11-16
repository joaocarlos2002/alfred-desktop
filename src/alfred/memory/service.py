"""Memória do ALFRED: o que ele lembra, sob controle total do usuário (RF-012).

Memórias (fatos e preferências) entram no contexto da IA com limite de tamanho; conversas ficam
registradas localmente. Tudo pode ser consultado, editado, excluído ou apagado de uma vez — e o
que é excluído deixa imediatamente de ser usado no contexto.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from alfred.core.event_bus import EventBus, Subscription
from alfred.core.orchestrator import AssistantReply
from alfred.domain.module import Module
from alfred.domain.tasks import TaskStarted
from alfred.infrastructure.db.memory_repository import MemoryItem

logger = logging.getLogger(__name__)

CONTEXT_CHAR_LIMIT = 2000
"""~500 tokens: memórias mais recentes primeiro, até o limite."""


class MemoryStore(Protocol):
    async def add(self, kind: str, content: str, source: str | None = None) -> MemoryItem: ...

    async def list(self) -> list[MemoryItem]: ...

    async def update(self, memory_id: int, content: str) -> bool: ...

    async def delete(self, memory_id: int) -> bool: ...

    async def add_message(self, conversation_id: str | None, role: str, content: str) -> str: ...

    async def forget_all(self) -> None: ...


class MemoryService(Module):
    name = "memory"
    depends_on = ("database", "event_bus")
    optional = True

    def __init__(self, store: MemoryStore, bus: EventBus) -> None:
        self._store = store
        self._bus = bus
        self._conversation: str | None = None
        self._lock = asyncio.Lock()  # handlers concorrentes compartilham a conversa
        self._subscriptions: list[Subscription[Any]] = []

    async def start(self) -> None:
        self._subscriptions = [
            self._bus.subscribe(TaskStarted, self._on_request, name="memory-requests"),
            self._bus.subscribe(AssistantReply, self._on_reply, name="memory-replies"),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()

    # ------------------------------------------------------------------ memórias
    async def remember(
        self, content: str, *, kind: str = "fact", source: str = "usuário"
    ) -> MemoryItem:
        return await self._store.add(kind, content.strip(), source)

    async def memories(self) -> list[MemoryItem]:
        return await self._store.list()

    async def edit(self, memory_id: int, content: str) -> bool:
        return await self._store.update(memory_id, content.strip())

    async def forget(self, memory_id: int) -> bool:
        return await self._store.delete(memory_id)

    async def forget_everything(self) -> None:
        async with self._lock:
            await self._store.forget_all()
            self._conversation = None
        logger.info("Memórias e conversas apagadas a pedido do usuário")

    async def context(self) -> str:
        """Bloco de contexto para a instrução de sistema da IA (vazio se não houver memórias)."""
        lines: list[str] = []
        used = 0
        for item in await self._store.list():
            line = f"- {item.content}"
            if used + len(line) > CONTEXT_CHAR_LIMIT:
                break
            lines.append(line)
            used += len(line)
        if not lines:
            return ""
        return "O que você sabe sobre o usuário (memórias que ele autorizou):\n" + "\n".join(lines)

    # ------------------------------------------------------------------ conversas
    async def _on_request(self, event: TaskStarted) -> None:
        if event.origin in ("voice", "ui"):
            await self._record("user", event.title)

    async def _on_reply(self, event: AssistantReply) -> None:
        await self._record("assistant", event.text)

    async def _record(self, role: str, content: str) -> None:
        async with self._lock:
            self._conversation = await self._store.add_message(self._conversation, role, content)
