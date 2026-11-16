"""Raiz de composição: instancia os módulos e conduz o ciclo de vida da aplicação."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from types import FrameType

from alfred.core.lifecycle import ModuleManager, StartupError
from alfred.domain.module import Module

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1
EXIT_ALREADY_RUNNING = 2

_SHUTDOWN_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, name)
)


class Application:
    """Inicia os módulos, aguarda um pedido de encerramento e encerra tudo de forma graciosa."""

    def __init__(self, manager: ModuleManager) -> None:
        self.manager = manager
        self._shutdown = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def request_shutdown(self) -> None:
        """Pede o encerramento. Seguro para chamar de qualquer thread."""
        if self._loop is None:
            self._shutdown.set()
        else:
            self._loop.call_soon_threadsafe(self._shutdown.set)

    async def run(self, *, install_signal_handlers: bool = True) -> int:
        self._loop = asyncio.get_running_loop()
        with _signal_handlers(self.request_shutdown, enabled=install_signal_handlers):
            try:
                await self.manager.start_all()
            except StartupError:
                logger.exception("ALFRED não pôde ser iniciado")
                return EXIT_STARTUP_FAILED
            logger.info("ALFRED em execução")
            try:
                await self._shutdown.wait()
            finally:
                logger.info("Encerrando ALFRED")
                await self.manager.stop_all()
        return EXIT_OK


def create_application(modules: Iterable[Module] = ()) -> Application:
    manager = ModuleManager()
    for module in modules:
        manager.register(module)
    return Application(manager)


@contextmanager
def _signal_handlers(callback: Callable[[], None], *, enabled: bool) -> Iterator[None]:
    if not enabled:
        yield
        return

    def handler(signum: int, frame: FrameType | None) -> None:
        logger.info("Sinal %s recebido", signal.Signals(signum).name)
        callback()

    previous = {sig: signal.signal(sig, handler) for sig in _SHUTDOWN_SIGNALS}
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
