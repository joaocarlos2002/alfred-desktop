"""Trava global de foco, teclado e mouse (RF-010).

Operações independentes podem rodar em paralelo, mas tudo que depende de foco ou de input
simulado é serializado: duas tarefas nunca disputam o teclado/mouse. Cancelar uma tarefa (inclusive
pela interrupção de emergência) libera a trava imediatamente.

A trava é reentrante dentro do mesmo contexto: uma etapa do orquestrador que já a segura pode
chamar serviços que também a pedem (ex.: ``InputService``) sem travar a si mesma.
"""

from __future__ import annotations

import asyncio
import contextvars
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
        self._holding = contextvars.ContextVar(f"input-lock-{id(self)}", default=False)

    @property
    def locked(self) -> bool:
        return self._lock.locked()

    @asynccontextmanager
    async def hold(self, owner: str, *, timeout: float = 30.0) -> AsyncIterator[None]:
        if self._holding.get():  # reentrada: quem chamou já segura a trava
            yield
            return
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout)
        except TimeoutError as exc:
            raise InputLockTimeoutError(
                f"Teclado/mouse ocupados por '{self.owner}' há mais de {timeout:.0f} s"
            ) from exc
        self.owner = owner
        token = self._holding.set(True)
        try:
            yield
        finally:
            self._holding.reset(token)
            self.owner = None
            self._lock.release()
