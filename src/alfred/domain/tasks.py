"""Eventos do ciclo de vida de tarefas e etapas (publicados pelo orquestrador)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from alfred.domain.events import Event


class TaskStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    """O ALFRED foi encerrado (ou caiu) com a tarefa em andamento."""


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class TasksInterrupted(Event):
    """No início: tarefas que ficaram pela metade na execução anterior."""

    count: int
    titles: tuple[str, ...] = ()


class TaskStarted(Event):
    task_id: UUID
    title: str
    origin: str  # voice | hotkey | trigger | ui


class TaskStepStarted(Event):
    task_id: UUID
    position: int
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


class TaskStepFinished(Event):
    task_id: UUID
    position: int
    status: StepStatus
    result: dict[str, Any] | None = None
    error: str | None = None


class TaskFinished(Event):
    task_id: UUID
    status: TaskStatus
    error: str | None = None
