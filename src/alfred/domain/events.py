"""Contratos de eventos trocados entre módulos pelo event bus."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

_correlation_id: ContextVar[UUID | None] = ContextVar("alfred_correlation_id", default=None)


def current_correlation_id() -> UUID | None:
    """Correlation id da cadeia de eventos em andamento, se houver."""
    return _correlation_id.get()


@contextmanager
def correlation_scope(correlation_id: UUID) -> Iterator[UUID]:
    """Faz com que eventos criados neste contexto herdem ``correlation_id``."""
    token = _correlation_id.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _correlation_id.reset(token)


def _inherit_or_new_correlation_id() -> UUID:
    return _correlation_id.get() or uuid4()


class Event(BaseModel):
    """Evento imutável. Subclasses adicionam os campos de payload.

    ``correlation_id`` é herdado automaticamente quando o evento é criado durante o tratamento
    de outro evento, ligando toda a cadeia de uma tarefa.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID = Field(default_factory=_inherit_or_new_correlation_id)
    source: str = "alfred"


class Query[ResultT](Event):
    """Evento de request/response: tem exatamente um respondedor e devolve ``ResultT``."""


class ApplicationStarted(Event):
    """Todos os módulos foram iniciados (avisos que precisam da interface vêm depois)."""
