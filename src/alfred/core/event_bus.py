"""Event bus assíncrono em processo (ADR-0001).

Cada assinatura tem fila limitada e worker próprios:

- a ordem de entrega é preservada por assinante;
- um handler lento ou com erro não afeta os demais;
- fila cheia aplica backpressure em ``publish`` (o publicador aguarda).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from alfred.domain.events import Event, Query, correlation_scope
from alfred.domain.module import Health, HealthStatus, Module

logger = logging.getLogger(__name__)

type Tap = Callable[[Event], None]


class EventBusError(Exception):
    pass


class NoResponderError(EventBusError):
    pass


class RequestTimeoutError(EventBusError, TimeoutError):
    pass


class Subscription[EventT: Event]:
    """Assinatura ativa. Use ``unsubscribe()`` para cancelar."""

    def __init__(
        self,
        bus: EventBus,
        event_type: type[EventT],
        handler: Callable[[EventT], Awaitable[None]],
        name: str,
        queue_size: int,
    ) -> None:
        self.event_type = event_type
        self.name = name
        self.delivered = 0
        self.failures = 0
        self._bus = bus
        self._handler = handler
        self._queue: asyncio.Queue[EventT] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def unsubscribe(self) -> None:
        self._bus._remove(self)

    def _start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name=f"event-bus:{self.name}")

    async def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _put(self, event: EventT) -> None:
        await self._queue.put(event)

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                with correlation_scope(event.correlation_id):
                    await self._handler(event)
                self.delivered += 1
            except Exception:
                self.failures += 1
                logger.exception(
                    "Handler '%s' falhou ao tratar %s (correlation_id=%s)",
                    self.name,
                    type(event).__name__,
                    event.correlation_id,
                )
            finally:
                self._queue.task_done()


class EventBus(Module):
    name = "event_bus"

    def __init__(self, *, queue_size: int = 1000, drain_timeout: float = 2.0) -> None:
        self._queue_size = queue_size
        self._drain_timeout = drain_timeout
        self._subscriptions: list[Subscription[Any]] = []
        self._responders: dict[type[Event], Callable[[Any], Awaitable[Any]]] = {}
        self._taps: list[Tap] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for subscription in self._subscriptions:
            subscription._start()

    async def stop(self) -> None:
        try:
            await self.drain(timeout=self._drain_timeout)
        except TimeoutError:
            pending = sum(s.pending for s in self._subscriptions)
            logger.warning("Event bus encerrado com %d evento(s) não entregue(s)", pending)
        for subscription in self._subscriptions:
            await subscription._cancel()
        self._loop = None

    async def health(self) -> Health:
        backlog = max((s.pending for s in self._subscriptions), default=0)
        if backlog >= self._queue_size:
            return Health(status=HealthStatus.DEGRADED, detail=f"fila cheia ({backlog})")
        return Health(status=HealthStatus.HEALTHY)

    @property
    def running(self) -> bool:
        return self._loop is not None

    # ------------------------------------------------------------------ pub/sub
    def subscribe[EventT: Event](
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], Awaitable[None]],
        *,
        name: str | None = None,
    ) -> Subscription[EventT]:
        """Assina ``event_type`` e suas subclasses. ``subscribe(Event, ...)`` observa tudo."""
        label = name or str(getattr(handler, "__qualname__", repr(handler)))
        subscription = Subscription(self, event_type, handler, label, self._queue_size)
        self._subscriptions.append(subscription)
        if self.running:
            subscription._start()
        return subscription

    def add_tap(self, tap: Tap) -> None:
        """Observador síncrono chamado na publicação de todo evento (auditoria)."""
        self._taps.append(tap)

    async def publish(self, event: Event) -> None:
        self._notify_taps(event)
        for subscription in list(self._subscriptions):
            if isinstance(event, subscription.event_type):
                await subscription._put(event)

    def publish_threadsafe(self, event: Event) -> None:
        """Publica a partir de outra thread (ex.: listener de atalhos Win32)."""
        if self._loop is None:
            raise EventBusError("Event bus não está em execução")
        asyncio.run_coroutine_threadsafe(self.publish(event), self._loop)

    async def drain(self, *, timeout: float | None = None) -> None:
        """Aguarda até que todos os eventos enfileirados tenham sido tratados."""
        joins = [s._queue.join() for s in self._subscriptions]
        await asyncio.wait_for(asyncio.gather(*joins), timeout)

    # ------------------------------------------------------------------ request/response
    def respond[QueryT: Query[Any]](
        self, query_type: type[QueryT], handler: Callable[[QueryT], Awaitable[Any]]
    ) -> None:
        if query_type in self._responders:
            raise EventBusError(f"{query_type.__name__} já possui um respondedor")
        self._responders[query_type] = handler

    async def request[ResultT](self, query: Query[ResultT], *, timeout: float = 5.0) -> ResultT:
        handler = self._responders.get(type(query))
        if handler is None:
            raise NoResponderError(f"Nenhum respondedor para {type(query).__name__}")
        self._notify_taps(query)
        with correlation_scope(query.correlation_id):
            try:
                result = await asyncio.wait_for(handler(query), timeout)
            except TimeoutError as exc:
                raise RequestTimeoutError(
                    f"{type(query).__name__} sem resposta em {timeout:.1f} s"
                ) from exc
        return cast(ResultT, result)

    # ------------------------------------------------------------------ internos
    def _remove(self, subscription: Subscription[Any]) -> None:
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)
            if subscription._worker is not None:
                subscription._worker.cancel()
                subscription._worker = None

    def _notify_taps(self, event: Event) -> None:
        for tap in self._taps:
            try:
                tap(event)
            except Exception:
                logger.exception("Tap do event bus falhou")
