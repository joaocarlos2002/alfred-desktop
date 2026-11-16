"""Raiz de composição: instancia os módulos e conduz o ciclo de vida da aplicação."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from types import FrameType

from alfred.core.event_bus import EventBus
from alfred.core.lifecycle import ModuleManager, StartupError
from alfred.domain.events import Event
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

    def __init__(self, manager: ModuleManager, bus: EventBus) -> None:
        self.manager = manager
        self.bus = bus
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


def create_application(
    modules: Iterable[Module] = (), *, bus: EventBus | None = None
) -> Application:
    """Monta a aplicação. O event bus é sempre o primeiro módulo registrado."""
    bus = bus or EventBus()
    bus.add_tap(_audit_event)
    manager = ModuleManager()
    manager.register(bus)
    for module in modules:
        manager.register(module)
    return Application(manager, bus)


_event_logger = logging.getLogger("alfred.events")


def _audit_event(event: Event) -> None:
    """Registra todo evento publicado (nível DEBUG) para auditoria."""
    if _event_logger.isEnabledFor(logging.DEBUG):
        _event_logger.debug(
            "%s",
            type(event).__name__,
            extra={
                "event_id": str(event.id),
                "event_correlation_id": str(event.correlation_id),
                "event_source": event.source,
            },
        )


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
