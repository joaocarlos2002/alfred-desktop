"""Políticas de permissão persistidas na tabela ``permissions``."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import Permission


class SqlPermissionRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def policies(self) -> dict[tuple[str, str | None], str]:
        def work(session: Session) -> dict[tuple[str, str | None], str]:
            return {(p.category, p.tool): p.policy for p in session.scalars(select(Permission))}

        return await self._db.run(work)

    async def set_policy(self, category: str, tool: str | None, policy: str) -> None:
        def work(session: Session) -> None:
            query = select(Permission).where(Permission.category == category)
            query = query.where(
                Permission.tool.is_(None) if tool is None else Permission.tool == tool
            )
            row = session.scalars(query).first()
            if row is None:
                session.add(Permission(category=category, tool=tool, policy=policy))
            else:
                row.policy = policy

        await self._db.run(work)
