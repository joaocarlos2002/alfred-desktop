"""Implementação SQLite do ``ActivityLogRepository``."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Delete, delete, select
from sqlalchemy.orm import Session

from alfred.domain.activity import ActivityEntry, ActivityLogRepository, ActivityStatus
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import ActivityLog


def _to_entry(row: ActivityLog) -> ActivityEntry:
    return ActivityEntry(
        id=row.id,
        timestamp=row.timestamp,
        correlation_id=UUID(row.correlation_id) if row.correlation_id else None,
        category=row.category,
        action=row.action,
        status=ActivityStatus(row.status),
        detail=row.detail,
    )


def _deleted(session: Session, statement: Delete) -> int:
    result = cast(CursorResult[Any], session.execute(statement))
    return int(result.rowcount)


class SqlActivityLogRepository(ActivityLogRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        def work(session: Session) -> ActivityEntry:
            row = ActivityLog(
                timestamp=entry.timestamp,
                correlation_id=str(entry.correlation_id) if entry.correlation_id else None,
                category=entry.category,
                action=entry.action,
                status=entry.status.value,
                detail=entry.detail,
            )
            session.add(row)
            session.flush()
            return _to_entry(row)

        return await self._db.run(work)

    async def list(
        self,
        *,
        correlation_id: UUID | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[ActivityEntry]:
        def work(session: Session) -> list[ActivityEntry]:
            query = select(ActivityLog).order_by(
                ActivityLog.timestamp.desc(), ActivityLog.id.desc()
            )
            if correlation_id is not None:
                query = query.where(ActivityLog.correlation_id == str(correlation_id))
            if since is not None:
                query = query.where(ActivityLog.timestamp >= since)
            return [_to_entry(row) for row in session.scalars(query.limit(limit))]

        return await self._db.run(work)

    async def delete(self, entry_id: int) -> bool:
        def work(session: Session) -> bool:
            return _deleted(session, delete(ActivityLog).where(ActivityLog.id == entry_id)) > 0

        return await self._db.run(work)

    async def delete_older_than(self, cutoff: datetime) -> int:
        def work(session: Session) -> int:
            return _deleted(session, delete(ActivityLog).where(ActivityLog.timestamp < cutoff))

        return await self._db.run(work)
