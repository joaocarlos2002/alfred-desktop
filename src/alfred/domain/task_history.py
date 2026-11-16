"""Histórico de tarefas: registros de leitura e contrato de repositório (RF-012, RF-014)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from alfred.domain.tasks import StepStatus, TaskStatus


class StepRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    position: int
    tool: str
    status: StepStatus
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    correlation_id: UUID
    title: str
    status: TaskStatus
    origin: str
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    steps: tuple[StepRecord, ...] = ()

    @property
    def completed_steps(self) -> int:
        """Etapas efetivamente concluídas — a única medida de progresso exibida."""
        return sum(1 for step in self.steps if step.status is StepStatus.SUCCEEDED)


class TaskRepository(Protocol):
    async def task_started(
        self, *, task_id: UUID, correlation_id: UUID, title: str, origin: str, at: datetime
    ) -> None: ...

    async def step_started(
        self, *, task_id: UUID, position: int, tool: str, params: dict[str, Any], at: datetime
    ) -> None: ...

    async def step_finished(
        self,
        *,
        task_id: UUID,
        position: int,
        status: StepStatus,
        result: dict[str, Any] | None,
        error: str | None,
        at: datetime,
    ) -> None: ...

    async def task_finished(
        self, *, task_id: UUID, status: TaskStatus, error: str | None, at: datetime
    ) -> None: ...

    async def list_tasks(
        self, *, status: TaskStatus | None = None, since: datetime | None = None, limit: int = 100
    ) -> list[TaskRecord]:
        """Mais recentes primeiro, com as etapas."""
        ...

    async def mark_interrupted(self, *, at: datetime) -> list[str]:
        """Marca como interrompidas as tarefas (e etapas) que ficaram 'running' após um
        encerramento inesperado. Devolve os títulos das tarefas."""
        ...
