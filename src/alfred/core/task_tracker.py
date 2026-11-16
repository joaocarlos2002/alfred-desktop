"""Persiste o ciclo de vida real das tarefas a partir dos eventos do orquestrador (RF-014)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.events import ApplicationStarted, Event
from alfred.domain.module import Module
from alfred.domain.task_history import TaskRepository
from alfred.domain.tasks import (
    TaskFinished,
    TasksInterrupted,
    TaskStarted,
    TaskStepFinished,
    TaskStepStarted,
)
from alfred.infrastructure.logging_setup import REDACTED, redactor

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = ("password", "senha", "secret", "token", "api_key", "apikey", "credential")


def mask_sensitive(value: Any) -> Any:
    """Mascara campos com nome sensível e segredos reconhecidos em textos."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if any(s in str(key).lower() for s in SENSITIVE_KEYS)
            else mask_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return redactor.redact(value)
    return value


class TaskTracker(Module):
    name = "task_tracker"
    depends_on = ("event_bus", "database")

    def __init__(self, bus: EventBus, repository: TaskRepository) -> None:
        self.interrupted: list[str] = []
        self._bus = bus
        self._repository = repository
        self._subscription: Subscription[Event] | None = None

    async def start(self) -> None:
        self.interrupted = await self._repository.mark_interrupted(at=datetime.now(UTC))
        if self.interrupted:
            logger.warning(
                "%d tarefa(s) interrompida(s) na execução anterior", len(self.interrupted)
            )
        self._subscription = self._bus.subscribe(Event, self._on_event, name="task-tracker")

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()

    async def _on_event(self, event: Event) -> None:
        at = event.timestamp
        match event:
            case ApplicationStarted() if self.interrupted:
                # Avisa só agora, quando a interface já está ouvindo. Nada é retomado sozinho:
                # repetir ações pela metade sem o usuário pedir não é seguro.
                titles, self.interrupted = tuple(self.interrupted), []
                await self._bus.publish(TasksInterrupted(count=len(titles), titles=titles))
            case TaskStarted():
                await self._repository.task_started(
                    task_id=event.task_id,
                    correlation_id=event.correlation_id,
                    title=event.title,
                    origin=event.origin,
                    at=at,
                )
            case TaskStepStarted():
                await self._repository.step_started(
                    task_id=event.task_id,
                    position=event.position,
                    tool=event.tool,
                    params=mask_sensitive(event.params),
                    at=at,
                )
            case TaskStepFinished():
                await self._repository.step_finished(
                    task_id=event.task_id,
                    position=event.position,
                    status=event.status,
                    result=mask_sensitive(event.result) if event.result is not None else None,
                    error=redactor.redact(event.error) if event.error else None,
                    at=at,
                )
            case TaskFinished():
                await self._repository.task_finished(
                    task_id=event.task_id,
                    status=event.status,
                    error=redactor.redact(event.error) if event.error else None,
                    at=at,
                )
