"""Implementação SQLite do ``TaskRepository``."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from alfred.domain.task_history import StepRecord, TaskRecord, TaskRepository
from alfred.domain.tasks import StepStatus, TaskStatus
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import Task, TaskStep


def _to_record(row: Task) -> TaskRecord:
    return TaskRecord(
        id=UUID(row.id),
        correlation_id=UUID(row.correlation_id),
        title=row.title,
        status=TaskStatus(row.status),
        origin=row.source,
        error=row.error,
        created_at=row.created_at,
        finished_at=row.finished_at,
        steps=tuple(
            StepRecord(
                position=step.position,
                tool=step.tool,
                status=StepStatus(step.status),
                params=step.params,
                result=step.result,
                error=step.error,
                started_at=step.started_at,
                finished_at=step.finished_at,
            )
            for step in row.steps
        ),
    )


class SqlTaskRepository(TaskRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    async def task_started(
        self, *, task_id: UUID, correlation_id: UUID, title: str, origin: str, at: datetime
    ) -> None:
        def work(session: Session) -> None:
            session.add(
                Task(
                    id=str(task_id),
                    correlation_id=str(correlation_id),
                    title=title,
                    status=TaskStatus.RUNNING.value,
                    source=origin,
                    created_at=at,
                    started_at=at,
                )
            )

        await self._db.run(work)

    async def step_started(
        self, *, task_id: UUID, position: int, tool: str, params: dict[str, Any], at: datetime
    ) -> None:
        def work(session: Session) -> None:
            session.add(
                TaskStep(
                    task_id=str(task_id),
                    position=position,
                    tool=tool,
                    status=StepStatus.RUNNING.value,
                    params=params,
                    started_at=at,
                )
            )

        await self._db.run(work)

    async def step_finished(
        self,
        *,
        task_id: UUID,
        position: int,
        status: StepStatus,
        result: dict[str, Any] | None,
        error: str | None,
        at: datetime,
    ) -> None:
        def work(session: Session) -> None:
            session.execute(
                update(TaskStep)
                .where(TaskStep.task_id == str(task_id), TaskStep.position == position)
                .values(status=status.value, result=result, error=error, finished_at=at)
            )

        await self._db.run(work)

    async def task_finished(
        self, *, task_id: UUID, status: TaskStatus, error: str | None, at: datetime
    ) -> None:
        def work(session: Session) -> None:
            session.execute(
                update(Task)
                .where(Task.id == str(task_id))
                .values(status=status.value, error=error, finished_at=at)
            )

        await self._db.run(work)

    async def list_tasks(
        self, *, status: TaskStatus | None = None, since: datetime | None = None, limit: int = 100
    ) -> list[TaskRecord]:
        def work(session: Session) -> list[TaskRecord]:
            query = (
                select(Task)
                .options(selectinload(Task.steps))
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
            if status is not None:
                query = query.where(Task.status == status.value)
            if since is not None:
                query = query.where(Task.created_at >= since)
            return [_to_record(row) for row in session.scalars(query)]

        return await self._db.run(work)

    async def mark_interrupted(self, *, at: datetime) -> int:
        def work(session: Session) -> int:
            running = session.scalars(
                select(Task).where(Task.status == TaskStatus.RUNNING.value)
            ).all()
            for task in running:
                task.status = TaskStatus.CANCELLED.value
                task.error = "Interrompida: o ALFRED foi encerrado durante a execução"
                task.finished_at = at
            return len(running)

        return await self._db.run(work)
