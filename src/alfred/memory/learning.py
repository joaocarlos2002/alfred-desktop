"""Aprendizado contínuo (#120): extrai preferências e fatos das conversas, sob controle do usuário.

Depois de cada troca **por voz ou pela interface**, o modelo (local por padrão) recebe só a fala
do usuário — nunca a resposta do assistente, que pode carregar texto de páginas, janelas ou da tela
(proteção contra prompt injection, threat model T1). Ele devolve fatos/preferências duráveis com
confiança e, se for o caso, qual memória existente a nova substitui.

Modos (``ai.learning_mode``): ``suggest`` (padrão) guarda como sugestão para o usuário aprovar,
editar ou descartar na seção Memória; ``auto`` grava direto; ``off`` não aprende nada.
Sugestões pendentes não entram no contexto da IA. Tudo fica no SQLite local.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any, Protocol
from uuid import UUID

from alfred.config.settings import AISettings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.orchestrator import AssistantReply
from alfred.domain.ai import ChatMessage
from alfred.domain.module import Module
from alfred.domain.tasks import TaskStarted
from alfred.memory.service import MemoryService

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.6
SIMILAR = 0.85
"""Acima disso (texto normalizado), é a mesma memória: não guarda de novo."""
KINDS = {"preferencia": "preference", "preferência": "preference", "fato": "fact"}
CONVERSATIONAL = frozenset({"voice", "ui"})
WAKE = re.compile(r"^\s*(?:ok\s+|ei\s+|oi\s+)?alfred[\s,.!?:]*", re.IGNORECASE)
"""Nome do assistente no início da fala (não é informação sobre o usuário)."""

EXTRACTION_PROMPT = """Você extrai memórias duráveis sobre o USUÁRIO a partir do que ele disse.
O usuário conversa com um assistente chamado ALFRED: "Alfred" é o nome do assistente, NUNCA do
usuário. Escreva cada memória sobre "O usuário" (ex.: "O usuário mora em Recife").
Considere só fatos estáveis (onde mora, programas que usa, nomes importantes) e preferências
(estilo de resposta, horários, formas de tratamento). Ignore pedidos pontuais e perguntas.
Não invente.

Memórias que você já tem (id entre colchetes):
{existing}

Se a fala CORRIGE uma memória existente, use "substitui" com o id dela. Exemplo: com
"- [7] O usuário usa o Chrome" e a fala "troquei o Chrome pelo Firefox", responda
{{"itens": [{{"tipo": "preferencia", "texto": "O usuário usa o Firefox", "confianca": 0.9,
"substitui": 7}}]}}

Responda SOMENTE com JSON no formato:
{{"itens": [{{"tipo": "preferencia" | "fato", "texto": "frase curta sobre o usuário",
"confianca": 0.0-1.0, "substitui": id_ou_null}}]}}
Se não houver nada a lembrar, responda {{"itens": []}}."""


class Chat(Protocol):
    async def chat(self, messages: list[ChatMessage], *, tools: Any = None) -> Any: ...


def parse_items(text: str) -> list[dict[str, Any]]:
    """Primeiro objeto JSON com ``itens`` (modelos às vezes cercam o JSON de texto)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("itens") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


class ConversationLearner(Module):
    name = "learning"
    depends_on = ("memory", "ai", "event_bus")
    optional = True

    def __init__(
        self,
        memory: MemoryService,
        chat: Chat,
        bus: EventBus,
        *,
        config: Callable[[], AISettings] = AISettings,
    ) -> None:
        self._memory = memory
        self._chat = chat
        self._bus = bus
        self._config = config
        self._requests: dict[UUID, str] = {}
        self._pending: set[asyncio.Task[None]] = set()
        self._subscriptions: list[Subscription[Any]] = []

    async def start(self) -> None:
        self._subscriptions = [
            self._bus.subscribe(TaskStarted, self._on_task, name="learning-requests"),
            self._bus.subscribe(AssistantReply, self._on_reply, name="learning-replies"),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        await self.idle()

    async def idle(self) -> None:
        """Espera as extrações em andamento (testes e encerramento)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def _on_task(self, event: TaskStarted) -> None:
        if event.origin in CONVERSATIONAL:
            self._requests[event.task_id] = event.title

    async def _on_reply(self, event: AssistantReply) -> None:
        request = self._requests.pop(event.task_id, None)
        if request is None or self._config().learning_mode == "off":
            return
        # Em segundo plano: aprender nunca atrasa a resposta ao usuário.
        task = asyncio.create_task(self._learn(request), name="memoria:aprender")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _learn(self, request: str) -> None:
        try:
            await self._extract(request)
        except Exception:
            logger.exception("Falha ao aprender com a conversa (ignorada)")

    async def _extract(self, request: str) -> None:
        mode = self._config().learning_mode
        known = await self._memory.memories()
        pending = await self._memory.suggestions()
        listing = "\n".join(f"- [{m.id}] {m.content}" for m in known) or "(nenhuma)"
        messages = [
            ChatMessage(role="system", content=EXTRACTION_PROMPT.format(existing=listing)),
            ChatMessage(role="user", content=f"O usuário disse: {WAKE.sub('', request).strip()}"),
        ]
        response = await self._chat.chat(messages)
        seen = [_norm(m.content) for m in (*known, *pending)]
        known_ids = {m.id for m in known}
        for item in parse_items(str(response.message.content)):
            kind = KINDS.get(str(item.get("tipo", "")).casefold())
            text = str(item.get("texto", "")).strip()
            try:
                confidence = float(item.get("confianca", 0))
            except (TypeError, ValueError):
                continue
            if kind is None or not 5 <= len(text) <= 200 or confidence < MIN_CONFIDENCE:
                continue
            normalized = _norm(text)
            if any(SequenceMatcher(None, normalized, other).ratio() >= SIMILAR for other in seen):
                continue  # já sabe disso
            replaces = item.get("substitui")
            replaces_id = replaces if isinstance(replaces, int) and replaces in known_ids else None
            if mode == "auto":
                await self._memory.learn(kind, text, confidence, replaces_id)
            else:
                await self._memory.suggest(kind, text, confidence, replaces_id)
            seen.append(normalized)
            logger.info("Aprendido (%s, %.2f): %s", mode, confidence, text)
