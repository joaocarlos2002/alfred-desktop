"""Conhecimento sobre programas persistido em ``app_knowledge`` (skill #125)."""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import AppKnowledge
from alfred.infrastructure.db.types import utcnow


@dataclass(frozen=True)
class Knowledge:
    app_key: str
    title: str | None
    elements: list[dict[str, Any]] = field(default_factory=list)
    shortcuts: list[str] = field(default_factory=list)
    procedures: dict[str, str] = field(default_factory=dict)
    explored_at: datetime | None = None


def _to_knowledge(row: AppKnowledge) -> Knowledge:
    return Knowledge(
        app_key=row.app_key,
        title=row.title,
        elements=list((row.elements or {}).get("items", [])),
        shortcuts=list(row.shortcuts or []),
        procedures=dict(row.procedures or {}),
        explored_at=row.explored_at,
    )


class SqlAppKnowledgeRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def list(self) -> builtins.list[Knowledge]:
        def work(session: Session) -> builtins.list[Knowledge]:
            rows = session.scalars(select(AppKnowledge).order_by(AppKnowledge.app_key))
            return [_to_knowledge(r) for r in rows]

        return await self._db.run(work)

    async def save_exploration(
        self,
        app_key: str,
        title: str | None,
        elements: builtins.list[dict[str, Any]],
        shortcuts: builtins.list[str],
    ) -> Knowledge:
        """Grava o mapa da interface, preservando os procedimentos já ensinados."""

        def work(session: Session) -> Knowledge:
            row = self._row(session, app_key)
            row.title = title or row.title
            row.elements = {"items": elements}
            row.shortcuts = shortcuts
            row.explored_at = utcnow()
            session.flush()
            return _to_knowledge(row)

        return await self._db.run(work)

    async def add_procedure(
        self, app_key: str, title: str | None, name: str, steps: str
    ) -> Knowledge:
        def work(session: Session) -> Knowledge:
            row = self._row(session, app_key)
            if title and not row.title:
                row.title = title
            row.procedures = {**(row.procedures or {}), name: steps}
            session.flush()
            return _to_knowledge(row)

        return await self._db.run(work)

    async def delete_procedure(self, app_key: str, name: str) -> bool:
        def work(session: Session) -> bool:
            row = self._find(session, app_key)
            if row is None or name not in (row.procedures or {}):
                return False
            row.procedures = {k: v for k, v in row.procedures.items() if k != name}
            return True

        return await self._db.run(work)

    async def delete(self, app_key: str) -> bool:
        def work(session: Session) -> bool:
            row = self._find(session, app_key)
            if row is None:
                return False
            session.delete(row)
            return True

        return await self._db.run(work)

    @staticmethod
    def _find(session: Session, app_key: str) -> AppKnowledge | None:
        return session.scalars(select(AppKnowledge).where(AppKnowledge.app_key == app_key)).first()

    @classmethod
    def _row(cls, session: Session, app_key: str) -> AppKnowledge:
        row = cls._find(session, app_key)
        if row is None:
            row = AppKnowledge(app_key=app_key, elements={"items": []}, shortcuts=[], procedures={})
            session.add(row)
        return row
