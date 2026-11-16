"""Aliases de aplicações persistidos na tabela ``applications``."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import Application


class SqlApplicationRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def list_aliases(self) -> dict[str, list[str]]:
        def work(session: Session) -> dict[str, list[str]]:
            return {app.name: list(app.aliases) for app in session.scalars(select(Application))}

        return await self._db.run(work)

    async def add_alias(self, app_name: str, alias: str) -> None:
        def work(session: Session) -> None:
            app = session.scalars(select(Application).where(Application.name == app_name)).first()
            if app is None:
                session.add(Application(name=app_name, aliases=[alias]))
            elif alias not in app.aliases:
                app.aliases = [*app.aliases, alias]

        await self._db.run(work)
