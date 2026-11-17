"""Memória do ALFRED: o que ele lembra, sob controle total do usuário (RF-012).

Memórias (fatos e preferências) entram no contexto da IA com limite de tamanho; conversas ficam
registradas localmente. Tudo pode ser consultado, editado, excluído ou apagado de uma vez — e o
que é excluído deixa imediatamente de ser usado no contexto.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from alfred.config.settings import AISettings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.orchestrator import AssistantReply
from alfred.domain.ai import ChatMessage
from alfred.domain.module import Module
from alfred.domain.tasks import TaskStarted
from alfred.infrastructure.db.memory_repository import MemoryItem

logger = logging.getLogger(__name__)

CONTEXT_CHAR_LIMIT = 2000
SUGGESTION = "suggestion:"
"""Prefixo do tipo das memórias aprendidas aguardando aprovação."""
SUMMARY_ITEMS = 10
"""Pedidos antigos resumidos além da janela de trocas."""
SUMMARY_CHARS = 120
"""~500 tokens: memórias mais recentes primeiro, até o limite."""


class MemoryStore(Protocol):
    async def add(
        self,
        kind: str,
        content: str,
        source: str | None = None,
        *,
        confidence: float | None = None,
        conversation_id: str | None = None,
        replaces_id: int | None = None,
    ) -> MemoryItem: ...

    async def list(self) -> list[MemoryItem]: ...

    async def update(self, memory_id: int, content: str, kind: str | None = None) -> bool: ...

    async def delete(self, memory_id: int) -> bool: ...

    async def add_message(
        self, conversation_id: str | None, role: str, content: str, at: datetime | None = None
    ) -> str: ...

    async def recent_messages(
        self, conversation_id: str, limit: int
    ) -> builtins.list[tuple[str, str, datetime]]: ...

    async def latest_message(self) -> tuple[str, datetime] | None: ...

    async def forget_all(self) -> None: ...


class MemoryService(Module):
    name = "memory"
    depends_on = ("database", "event_bus")
    optional = True

    def __init__(
        self,
        store: MemoryStore,
        bus: EventBus,
        *,
        config: Callable[[], AISettings] = AISettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._bus = bus
        self._config = config
        self._clock = clock
        self._conversation: str | None = None
        self._last_at: datetime | None = None
        self._lock = asyncio.Lock()  # handlers concorrentes compartilham a conversa
        self._subscriptions: list[Subscription[Any]] = []

    async def start(self) -> None:
        latest = await self._store.latest_message()
        if latest is not None:  # continua a conversa se o ALFRED reiniciou há pouco
            conversation, at = latest
            at = at if at.tzinfo else at.replace(tzinfo=UTC)
            if not self._expired(at):
                self._conversation, self._last_at = conversation, at
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
        """Memórias confirmadas (sugestões pendentes ficam de fora)."""
        return [m for m in await self._store.list() if not m.kind.startswith(SUGGESTION)]

    # ------------------------------------------------------------------ aprendizado (#120)
    async def suggestions(self) -> list[MemoryItem]:
        return [m for m in await self._store.list() if m.kind.startswith(SUGGESTION)]

    async def suggest(
        self, kind: str, content: str, confidence: float, replaces_id: int | None = None
    ) -> MemoryItem:
        """Guarda algo aprendido para o usuário aprovar (não entra no contexto da IA)."""
        return await self._store.add(
            SUGGESTION + kind,
            content,
            "aprendido",
            confidence=confidence,
            conversation_id=self._conversation,
            replaces_id=replaces_id,
        )

    async def learn(
        self, kind: str, content: str, confidence: float, replaces_id: int | None = None
    ) -> None:
        """Modo automático: grava direto (substituindo a memória conflitante)."""
        confirmed = {m.id for m in await self.memories()}
        if replaces_id is not None and replaces_id in confirmed:
            await self._store.update(replaces_id, content, kind)
            return
        await self._store.add(
            kind,
            content,
            "aprendido",
            confidence=confidence,
            conversation_id=self._conversation,
        )

    async def approve(self, suggestion_id: int, content: str | None = None) -> None:
        suggestion = next(s for s in await self.suggestions() if s.id == suggestion_id)
        kind = suggestion.kind.removeprefix(SUGGESTION)
        text = (content or suggestion.content).strip()
        if suggestion.replaces_id is not None:
            await self._store.delete(suggestion.replaces_id)  # consolidação: troca a antiga
        await self._store.update(suggestion_id, text, kind)

    async def discard(self, suggestion_id: int) -> None:
        await self._store.delete(suggestion_id)

    async def edit(self, memory_id: int, content: str) -> bool:
        return await self._store.update(memory_id, content.strip())

    async def forget(self, memory_id: int) -> bool:
        return await self._store.delete(memory_id)

    async def forget_everything(self) -> None:
        async with self._lock:
            await self._store.forget_all()
            self._conversation = None
            self._last_at = None
        logger.info("Memórias e conversas apagadas a pedido do usuário")

    async def context(self) -> str:
        """Bloco de contexto para a instrução de sistema da IA (vazio se não houver memórias)."""
        lines: list[str] = []
        used = 0
        for item in await self.memories():
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
            now = self._clock()
            if self._expired(self._last_at):
                self._conversation = None  # pausa longa: assunto novo
            self._conversation = await self._store.add_message(
                self._conversation, role, content, now
            )
            self._last_at = now

    # ------------------------------------------------------------------ contexto da conversa
    def _expired(self, at: datetime | None) -> bool:
        if at is None:
            return True
        idle = timedelta(minutes=self._config().conversation_idle_minutes)
        return self._clock() - at > idle

    async def new_conversation(self) -> None:
        """Encerra o assunto atual: os próximos pedidos começam sem as trocas anteriores."""
        async with self._lock:
            self._conversation = None
            self._last_at = None
        logger.info("Nova conversa iniciada")

    async def history(self) -> list[ChatMessage]:
        """Trocas recentes da conversa atual para o contexto do modelo (#118).

        As mais recentes entram inteiras até ``conversation_char_budget``; as anteriores viram
        um resumo com os pedidos do usuário. Vazio fora de uma conversa ativa."""
        ai = self._config()
        conversation = self._conversation
        if not ai.conversation_turns or conversation is None or self._expired(self._last_at):
            return []
        pool = await self._store.recent_messages(
            conversation, ai.conversation_turns * 2 + SUMMARY_ITEMS * 2
        )
        kept: list[ChatMessage] = []
        used = 0
        window = pool[-ai.conversation_turns * 2 :]
        for role, content, _ in reversed(window):
            if role not in ("user", "assistant"):
                continue
            if used + len(content) > ai.conversation_char_budget:
                break
            kept.insert(0, ChatMessage(role=role, content=content))
            used += len(content)
        dropped = pool[: len(pool) - len(kept)]
        earlier = [c for r, c, _ in dropped if r == "user"][-SUMMARY_ITEMS:]
        if earlier:
            summary = "\n".join(f"- {c[:SUMMARY_CHARS]}" for c in earlier)
            kept.insert(
                0,
                ChatMessage(
                    role="system",
                    content="Antes, nesta mesma conversa, o usuário pediu:\n" + summary,
                ),
            )
        return kept
