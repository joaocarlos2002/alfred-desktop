"""Registro central das tarefas assíncronas em execução, para cancelamento global."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Toda tarefa cancelável do ALFRED (orquestrador, automações, gatilhos) nasce aqui."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._by_id: dict[UUID, asyncio.Task[Any]] = {}

    def spawn[T](
        self, coro: Coroutine[Any, Any, T], *, name: str, task_id: UUID | None = None
    ) -> asyncio.Task[T]:
        """``task_id`` liga a tarefa assíncrona à tarefa do domínio, para cancelamento pela UI."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if task_id is not None:
            self._by_id[task_id] = task
            task.add_done_callback(lambda _: self._by_id.pop(task_id, None))
        return task

    def is_running(self, task_id: UUID) -> bool:
        task = self._by_id.get(task_id)
        return task is not None and not task.done()

    def cancel(self, task_id: UUID) -> bool:
        """Cancela uma tarefa específica. Devolve ``False`` se ela não está em execução."""
        task = self._by_id.get(task_id)
        if task is None or task.done():
            return False
        task.cancel()
        logger.info("Tarefa '%s' cancelada pelo usuário", task.get_name())
        return True

    @property
    def running(self) -> int:
        return sum(1 for task in self._tasks if not task.done())

    async def cancel_all(self, *, timeout: float = 0.4) -> tuple[int, int]:
        """Cancela tudo e espera até ``timeout``. Devolve (canceladas, que não terminaram)."""
        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if not tasks:
            return 0, 0
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            logger.error("Tarefa '%s' não respondeu ao cancelamento", task.get_name())
        return len(tasks), len(pending)
