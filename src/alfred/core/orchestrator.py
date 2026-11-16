"""Orquestrador: a IA planeja, o orquestrador executa (Diretriz 5, ADR-0002).

O modelo só devolve chamadas de ferramenta. Cada chamada passa por validação (allowlist + schema),
permissão (com confirmação quando exigida) e execução com timeout, como uma etapa real da tarefa.
Resultados e erros voltam ao modelo até ele responder sem ferramentas (ou atingir o limite).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID, uuid4

from alfred.core.event_bus import EventBus
from alfred.core.permissions import PermissionService
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import ToolError, ToolRegistry
from alfred.domain.ai import ChatMessage, ChatResponse, ProviderError, ToolCall, ToolSpec
from alfred.domain.events import Event
from alfred.domain.module import Module
from alfred.domain.tasks import (
    StepStatus,
    TaskFinished,
    TaskStarted,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)
from alfred.domain.voice import SpeakRequested

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8

SYSTEM_PROMPT = """Você é o ALFRED, assistente pessoal no Windows 11, e fala português do Brasil.
Para agir no computador, use SEMPRE as ferramentas disponíveis; nunca diga que fez algo sem ter
recebido o resultado da ferramenta. Se uma ferramenta falhar ou for negada, explique isso ao usuário
com honestidade. Confirmações de ações sensíveis são pedidas pelo sistema, não por você.
Responda de forma breve e natural: sua resposta final será falada em voz alta."""


class Planner(Protocol):
    async def chat(
        self, messages: list[ChatMessage], *, tools: list[ToolSpec] | None = None
    ) -> ChatResponse: ...


class AssistantReply(Event):
    """Resposta final do ALFRED a um pedido (texto que também é falado)."""

    task_id: UUID
    text: str


class Orchestrator(Module):
    name = "orchestrator"
    depends_on = ("event_bus", "permissions")
    optional = True

    def __init__(
        self,
        planner: Planner,
        tools: ToolRegistry,
        permissions: PermissionService,
        tasks: TaskRegistry,
        bus: EventBus,
        *,
        max_iterations: int = MAX_ITERATIONS,
        speak: bool = True,
        context_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.context_provider = context_provider
        self._planner = planner
        self._tools = tools
        self._permissions = permissions
        self._tasks = tasks
        self._bus = bus
        self._max_iterations = max_iterations
        self._speak = speak

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def submit(self, request: str, *, origin: str = "voice") -> asyncio.Task[str]:
        """Cria a tarefa (cancelável pelo painel e pela emergência) e devolve o handle."""
        task_id = uuid4()
        return self._tasks.spawn(
            self.run(request, origin=origin, task_id=task_id),
            name=f"pedido:{request[:40]}",
            task_id=task_id,
        )

    async def run(self, request: str, *, origin: str = "voice", task_id: UUID | None = None) -> str:
        task_id = task_id or uuid4()
        await self._bus.publish(TaskStarted(task_id=task_id, title=request, origin=origin))
        system = SYSTEM_PROMPT
        if self.context_provider is not None:
            extra = await self.context_provider()
            if extra:
                system = f"{SYSTEM_PROMPT}\n\n{extra}"
        history = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=request),
        ]
        position = 0
        try:
            for _ in range(self._max_iterations):
                response = await self._planner.chat(history, tools=self._tools.specs())
                history.append(response.message)
                if not response.tool_calls:
                    reply = response.message.content.strip() or "Pronto."
                    await self._finish(task_id, TaskStatus.SUCCEEDED, reply)
                    return reply
                for call in response.tool_calls:
                    position += 1
                    result = await self._run_step(task_id, position, call)
                    history.append(
                        ChatMessage(
                            role="tool",
                            content=json.dumps(result, ensure_ascii=False, default=str),
                            tool_call_id=call.id,
                        )
                    )
            reply = "Não consegui concluir: o pedido exigiu passos demais."
            await self._finish(task_id, TaskStatus.FAILED, reply, error="limite de iterações")
            return reply
        except asyncio.CancelledError:
            await self._bus.publish(
                TaskFinished(task_id=task_id, status=TaskStatus.CANCELLED, error="cancelada")
            )
            raise
        except ProviderError as exc:
            reply = f"Não consegui falar com a inteligência artificial: {exc}"
            await self._finish(task_id, TaskStatus.FAILED, reply, error=str(exc))
            return reply

    async def _run_step(self, task_id: UUID, position: int, call: ToolCall) -> dict[str, Any]:
        await self._bus.publish(
            TaskStepStarted(
                task_id=task_id, position=position, tool=call.name, params=call.arguments
            )
        )
        try:
            tool, params = self._tools.validate(call)
        except ToolError as exc:
            return await self._step_failed(task_id, position, StepStatus.FAILED, str(exc))
        summary = (
            f"{tool.description.rstrip('.')} — {json.dumps(call.arguments, ensure_ascii=False)}"
        )
        if not await self._permissions.authorize(tool, summary):
            return await self._step_failed(
                task_id, position, StepStatus.DENIED, "ação não permitida pelo usuário"
            )
        try:
            result = await self._tools.execute(call, params)
        except asyncio.CancelledError:
            await self._bus.publish(
                TaskStepFinished(task_id=task_id, position=position, status=StepStatus.CANCELLED)
            )
            raise
        except Exception as exc:  # erro da ferramenta volta ao modelo como resultado
            return await self._step_failed(
                task_id, position, StepStatus.FAILED, f"{type(exc).__name__}: {exc}"
            )
        await self._bus.publish(
            TaskStepFinished(
                task_id=task_id, position=position, status=StepStatus.SUCCEEDED, result=result
            )
        )
        return {"ok": True, **result}

    async def _step_failed(
        self, task_id: UUID, position: int, status: StepStatus, error: str
    ) -> dict[str, Any]:
        await self._bus.publish(
            TaskStepFinished(task_id=task_id, position=position, status=status, error=error)
        )
        return {"ok": False, "erro": error}

    async def _finish(
        self, task_id: UUID, status: TaskStatus, reply: str, *, error: str | None = None
    ) -> None:
        await self._bus.publish(AssistantReply(task_id=task_id, text=reply))
        if self._speak:
            await self._bus.publish(SpeakRequested(text=reply))
        await self._bus.publish(TaskFinished(task_id=task_id, status=status, error=error))
