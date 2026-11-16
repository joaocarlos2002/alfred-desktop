"""Histórico de atividades: entidade e contrato de repositório (RF-012, RF-014)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ActivityStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class ActivityEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID | None = None
    category: str
    action: str
    status: ActivityStatus
    detail: dict[str, Any] = Field(default_factory=dict)


class ActivityLogRepository(Protocol):
    async def add(self, entry: ActivityEntry) -> ActivityEntry: ...

    async def list(
        self,
        *,
        correlation_id: UUID | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[ActivityEntry]:
        """Mais recentes primeiro."""
        ...

    async def delete(self, entry_id: int) -> bool: ...

    async def delete_older_than(self, cutoff: datetime) -> int: ...
