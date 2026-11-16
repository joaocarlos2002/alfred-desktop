"""Registro central das tarefas assíncronas em execução, para cancelamento global."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Toda tarefa cancelável do ALFRED (orquestrador, automações, gatilhos) nasce aqui."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn[T](self, coro: Coroutine[Any, Any, T], *, name: str) -> asyncio.Task[T]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

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
