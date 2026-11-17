"""Memórias e conversas persistidas localmente (RF-012)."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import Conversation, Memory, Message


@dataclass(frozen=True)
class MemoryItem:
    id: int
    kind: str
    content: str
    source: str | None
    updated_at: datetime


class SqlMemoryRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def add(self, kind: str, content: str, source: str | None = None) -> MemoryItem:
        def work(session: Session) -> MemoryItem:
            row = Memory(kind=kind, content=content, source=source)
            session.add(row)
            session.flush()
            return MemoryItem(row.id, row.kind, row.content, row.source, row.updated_at)

        return await self._db.run(work)

    async def list(self) -> list[MemoryItem]:
        def work(session: Session) -> list[MemoryItem]:
            rows = session.scalars(select(Memory).order_by(Memory.updated_at.desc()))
            return [MemoryItem(r.id, r.kind, r.content, r.source, r.updated_at) for r in rows]

        return await self._db.run(work)

    async def update(self, memory_id: int, content: str) -> bool:
        def work(session: Session) -> bool:
            row = session.get(Memory, memory_id)
            if row is None:
                return False
            row.content = content
            return True

        return await self._db.run(work)

    async def delete(self, memory_id: int) -> bool:
        def work(session: Session) -> bool:
            row = session.get(Memory, memory_id)
            if row is None:
                return False
            session.delete(row)
            return True

        return await self._db.run(work)

    async def add_message(
        self,
        conversation_id: str | None,
        role: str,
        content: str,
        at: datetime | None = None,
    ) -> str:
        def work(session: Session) -> str:
            cid = conversation_id
            if cid is None or session.get(Conversation, cid) is None:
                cid = cid or str(uuid4())
                session.add(Conversation(id=cid, title=content[:80] if role == "user" else None))
                session.flush()
            message = Message(conversation_id=cid, role=role, content=content)
            if at is not None:
                message.created_at = at
            session.add(message)
            return cid

        return await self._db.run(work)

    async def recent_messages(
        self, conversation_id: str, limit: int
    ) -> builtins.list[tuple[str, str, datetime]]:
        """Últimas ``limit`` mensagens da conversa, da mais antiga para a mais nova."""

        def work(session: Session) -> builtins.list[tuple[str, str, datetime]]:
            rows = session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc())
                .limit(limit)
            )
            return [(m.role, m.content, m.created_at) for m in rows][::-1]

        return await self._db.run(work)

    async def latest_message(self) -> tuple[str, datetime] | None:
        """(conversa, horário) da mensagem mais recente, para continuar após reiniciar."""

        def work(session: Session) -> tuple[str, datetime] | None:
            row = session.scalars(select(Message).order_by(Message.id.desc()).limit(1)).first()
            return (row.conversation_id, row.created_at) if row is not None else None

        return await self._db.run(work)

    async def conversations(self) -> builtins.list[tuple[str, str | None, int]]:
        def work(session: Session) -> builtins.list[tuple[str, str | None, int]]:
            rows = session.scalars(select(Conversation).order_by(Conversation.created_at.desc()))
            return [(c.id, c.title, len(c.messages)) for c in rows]

        return await self._db.run(work)

    async def forget_all(self) -> None:
        """Apaga memórias, conversas e mensagens ("esquecer tudo")."""

        def work(session: Session) -> None:
            session.execute(delete(Message))
            session.execute(delete(Conversation))
            session.execute(delete(Memory))

        await self._db.run(work)
