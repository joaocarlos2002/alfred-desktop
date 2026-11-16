"""Gatilhos e execuções persistidos em ``triggers`` / ``trigger_executions`` (RF-011)."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from alfred.domain.triggers import TriggerCondition, TriggerSpec
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import Task, Trigger, TriggerExecution
from alfred.infrastructure.db.types import utcnow

RUNNING = "running"
INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class ExecutionRecord:
    id: int
    trigger_id: int
    status: str
    error: str | None
    task_id: str | None
    started_at: datetime
    finished_at: datetime | None


def _spec(row: Trigger) -> TriggerSpec:
    action = row.action or {}
    return TriggerSpec(
        id=row.id,
        name=row.name,
        kind=row.kind,
        config=row.config or {},
        condition=TriggerCondition.model_validate(action.get("condicao") or {}),
        request=action.get("pedido", ""),
        enabled=row.enabled,
    )


def _action(spec: TriggerSpec) -> dict[str, object]:
    return {"pedido": spec.request, "condicao": spec.condition.model_dump(mode="json")}


class SqlTriggerRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def list(self) -> list[TriggerSpec]:
        def work(session: Session) -> list[TriggerSpec]:
            return [_spec(r) for r in session.scalars(select(Trigger).order_by(Trigger.name))]

        return await self._db.run(work)

    async def save(self, spec: TriggerSpec) -> TriggerSpec:
        """Cria (sem ``id``) ou atualiza. Raises: KeyError se o ``id`` não existir."""

        def work(session: Session) -> TriggerSpec:
            if spec.id is None:
                row = Trigger(name=spec.name)
                session.add(row)
            else:
                found = session.get(Trigger, spec.id)
                if found is None:
                    raise KeyError(spec.id)
                row = found
            row.name = spec.name
            row.kind = spec.kind
            row.config = dict(spec.config)
            row.action = _action(spec)
            row.enabled = spec.enabled
            session.flush()
            return _spec(row)

        return await self._db.run(work)

    async def delete(self, trigger_id: int) -> bool:
        def work(session: Session) -> bool:
            row = session.get(Trigger, trigger_id)
            if row is None:
                return False
            session.delete(row)
            return True

        return await self._db.run(work)

    async def start_execution(self, trigger_id: int) -> int:
        def work(session: Session) -> int:
            row = TriggerExecution(trigger_id=trigger_id, status=RUNNING)
            session.add(row)
            session.flush()
            return row.id

        return await self._db.run(work)

    async def finish_execution(
        self,
        execution_id: int,
        status: str,
        *,
        error: str | None = None,
        task_id: UUID | None = None,
    ) -> None:
        def work(session: Session) -> None:
            row = session.get(TriggerExecution, execution_id)
            if row is None:
                return
            row.status = status
            row.error = error
            row.finished_at = utcnow()
            if task_id is not None and session.get(Task, str(task_id)) is not None:
                row.task_id = str(task_id)

        await self._db.run(work)

    async def mark_interrupted(self) -> int:
        """Execuções que ficaram "running" (ALFRED encerrado no meio) viram "interrupted"."""

        def work(session: Session) -> int:
            result = session.execute(
                update(TriggerExecution)
                .where(TriggerExecution.status == RUNNING)
                .values(status=INTERRUPTED, finished_at=utcnow(), error="ALFRED foi encerrado")
            )
            return int(getattr(result, "rowcount", 0) or 0)

        return await self._db.run(work)

    async def executions(
        self, trigger_id: int | None = None, *, limit: int = 50
    ) -> builtins.list[ExecutionRecord]:
        def work(session: Session) -> builtins.list[ExecutionRecord]:
            query = select(TriggerExecution).order_by(TriggerExecution.id.desc()).limit(limit)
            if trigger_id is not None:
                query = query.where(TriggerExecution.trigger_id == trigger_id)
            return [
                ExecutionRecord(
                    r.id, r.trigger_id, r.status, r.error, r.task_id, r.started_at, r.finished_at
                )
                for r in session.scalars(query)
            ]

        return await self._db.run(work)
