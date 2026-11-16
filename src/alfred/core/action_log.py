"""Registro auditável de toda ação executada pelo ALFRED (seção 6, RF-014).

Uso pelas ferramentas de automação::

    async with action_log.record("automation", "open_app", params={"app": "Bloco de Notas"}) as rec:
        ...
        rec.result = {"pid": 1234}

Sucesso, falha ou cancelamento são gravados em ``activity_logs`` com duração, parâmetros e
resultado mascarados e o ``correlation_id`` da tarefa em andamento.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from alfred.core.task_tracker import mask_sensitive
from alfred.domain.activity import ActivityEntry, ActivityLogRepository, ActivityStatus
from alfred.domain.events import current_correlation_id
from alfred.infrastructure.logging_setup import redactor

logger = logging.getLogger(__name__)


@dataclass
class ActionRecord:
    category: str
    action: str
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    status: ActivityStatus = ActivityStatus.SUCCEEDED
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def deny(self, reason: str) -> None:
        """Marca a ação como recusada (ex.: confirmação negada) sem lançar exceção."""
        self.status = ActivityStatus.DENIED
        self.error = reason


class ActionLog:
    def __init__(self, repository: ActivityLogRepository) -> None:
        self._repository = repository

    @asynccontextmanager
    async def record(
        self, category: str, action: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[ActionRecord]:
        record = ActionRecord(category, action, dict(params or {}))
        started = time.perf_counter()
        try:
            yield record
        except asyncio.CancelledError:
            record.status, record.error = ActivityStatus.CANCELLED, "cancelada"
            raise
        except Exception as exc:
            record.status, record.error = ActivityStatus.FAILED, f"{type(exc).__name__}: {exc}"
            raise
        finally:
            await self._write(record, (time.perf_counter() - started) * 1000)

    async def _write(self, record: ActionRecord, duration_ms: float) -> None:
        detail: dict[str, Any] = {
            "params": mask_sensitive(record.params),
            "duration_ms": round(duration_ms, 1),
            **mask_sensitive(record.detail),
        }
        if record.result is not None:
            detail["result"] = mask_sensitive(record.result)
        if record.error:
            detail["error"] = redactor.redact(record.error)
        try:
            await self._repository.add(
                ActivityEntry(
                    correlation_id=current_correlation_id(),
                    category=record.category,
                    action=record.action,
                    status=record.status,
                    detail=detail,
                )
            )
        except Exception:
            logger.exception("Falha ao registrar a ação '%s'", record.action)

    async def export(self, directory: Path, *, since: datetime | None = None) -> Path:
        """Exporta o histórico (já mascarado) em JSON Lines; devolve o arquivo criado."""
        entries = await self._repository.list(since=since, limit=1_000_000)
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = directory / f"atividades-{stamp}.jsonl"
        lines = [
            json.dumps(entry.model_dump(mode="json"), ensure_ascii=False)
            for entry in reversed(entries)
        ]
        content = "\n".join(lines) + ("\n" if lines else "")
        await asyncio.to_thread(path.write_text, content, "utf-8")
        logger.info("Histórico exportado para %s (%d registros)", path, len(lines))
        return path
