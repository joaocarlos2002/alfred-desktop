"""Contratos de confirmação explícita para ações sensíveis (seção 6)."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from alfred.domain.events import Event


class ConfirmationDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    """Sem resposta no prazo: tratado como recusa."""


class ConfirmationRequested(Event):
    request_id: UUID = Field(default_factory=uuid4)
    action: str
    """O que será feito, em linguagem clara (ex.: "Excluir arquivo")."""
    target: str
    """Sobre o quê (ex.: caminho do arquivo, destinatário)."""
    impact: str
    """Consequência (ex.: "A exclusão não pode ser desfeita")."""
    category: str
    timeout_seconds: float


class ConfirmationResolved(Event):
    request_id: UUID
    decision: ConfirmationDecision
