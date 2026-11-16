"""Trava global de foco, teclado e mouse (RF-010).

Operações independentes podem rodar em paralelo, mas tudo que depende de foco ou de input
simulado é serializado: duas tarefas nunca disputam o teclado/mouse. Cancelar uma tarefa (inclusive
pela interrupção de emergência) libera a trava imediatamente.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class InputLockTimeoutError(TimeoutError):
    pass


class InputLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.owner: str | None = None

    @property
    def locked(self) -> bool:
        return self._lock.locked()

    @asynccontextmanager
    async def hold(self, owner: str, *, timeout: float = 30.0) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout)
        except TimeoutError as exc:
            raise InputLockTimeoutError(
                f"Teclado/mouse ocupados por '{self.owner}' há mais de {timeout:.0f} s"
            ) from exc
        self.owner = owner
        try:
            yield
        finally:
            self.owner = None
            self._lock.release()
