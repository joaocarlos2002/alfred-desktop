"""Schema SQLite (seção 7 da especificação).

As configurações ficam em ``settings.json`` e não numa tabela (ADR-0004). Credenciais nunca são
armazenadas aqui: ``ai_providers.credential_ref`` guarda apenas o nome da entrada no Windows
Credential Manager.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from alfred.infrastructure.db.types import UTCDateTime, utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

type JsonDict = dict[str, Any]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UTCDateTime, JsonDict: JSON, list[str]: JSON}  # noqa: RUF012


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AIProvider(TimestampMixin, Base):
    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # local | external
    base_url: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    credential_ref: Mapped[str | None] = mapped_column(String(200))
    options: Mapped[JsonDict] = mapped_column(default=dict)


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(300))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), index=True)
    source: Mapped[str] = mapped_column(String(30))  # voice | hotkey | trigger | ui
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskStep.position"
    )


class TaskStep(Base):
    __tablename__ = "task_steps"
    __table_args__ = (UniqueConstraint("task_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    params: Mapped[JsonDict] = mapped_column(default=dict)
    result: Mapped[JsonDict | None]
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    task: Mapped[Task] = relationship(back_populates="steps")


class Application(TimestampMixin, Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    executable: Mapped[str | None] = mapped_column(String(1000))
    app_user_model_id: Mapped[str | None] = mapped_column(String(500))
    aliases: Mapped[list[str]] = mapped_column(default=list)


class AppKnowledge(TimestampMixin, Base):
    """O que o ALFRED aprendeu sobre um programa (#125): mapa da interface e procedimentos."""

    __tablename__ = "app_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    app_key: Mapped[str] = mapped_column(String(200), unique=True)
    title: Mapped[str | None] = mapped_column(String(300))
    elements: Mapped[JsonDict] = mapped_column(default=dict)
    shortcuts: Mapped[list[str]] = mapped_column(default=list)
    procedures: Mapped[JsonDict] = mapped_column(default=dict)
    explored_at: Mapped[datetime | None]


class Trigger(TimestampMixin, Base):
    __tablename__ = "triggers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    config: Mapped[JsonDict] = mapped_column(default=dict)
    action: Mapped[JsonDict] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    executions: Mapped[list[TriggerExecution]] = relationship(
        back_populates="trigger", cascade="all, delete-orphan"
    )


class TriggerExecution(Base):
    __tablename__ = "trigger_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_id: Mapped[int] = mapped_column(
        ForeignKey("triggers.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]

    trigger: Mapped[Trigger] = relationship(back_populates="executions")


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    __table_args__ = (Index("ix_activity_logs_timestamp", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(default=utcnow)
    correlation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    category: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    detail: Mapped[JsonDict] = mapped_column(default=dict)


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("category", "tool"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(50))
    tool: Mapped[str | None] = mapped_column(String(200))
    policy: Mapped[str] = mapped_column(String(10))  # allow | ask | deny
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Memory(TimestampMixin, Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # fact | preference | note | suggestion:*
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[float | None] = mapped_column(Float)
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    """Conversa de onde a memória foi aprendida (#120)."""
    replaces_id: Mapped[int | None]
    """Memória que esta substitui ao ser aprovada (consolidação)."""
