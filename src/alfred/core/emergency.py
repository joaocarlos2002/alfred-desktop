"""Interrupção de emergência global (RF-015).

O gatilho chega direto da thread do atalho para o event loop (``trigger_threadsafe``), sem passar
pela fila do event bus, para que um backlog de eventos nunca atrase a parada.
"""

from __future__ import annotations

import asyncio
import logging
import time

from alfred.core.event_bus import EventBus
from alfred.core.tasks import TaskRegistry
from alfred.domain.activity import ActivityEntry, ActivityLogRepository, ActivityStatus
from alfred.domain.emergency import EmergencyStopped, InputReleaser
from alfred.domain.module import Module

logger = logging.getLogger(__name__)


class EmergencyStop(Module):
    name = "emergency"

    def __init__(
        self,
        tasks: TaskRegistry,
        bus: EventBus,
        *,
        input_releaser: InputReleaser | None = None,
        activity: ActivityLogRepository | None = None,
        cancel_timeout: float = 0.4,
    ) -> None:
        self.depends_on = ("event_bus", "database") if activity else ("event_bus",)
        self._tasks = tasks
        self._bus = bus
        self._releaser = input_releaser
        self._activity = activity
        self._cancel_timeout = cancel_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running: asyncio.Task[EmergencyStopped] | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def stop(self) -> None:
        if self._running is not None:
            await asyncio.gather(self._running, return_exceptions=True)
        self._loop = None

    def trigger_threadsafe(self, trigger: str = "hotkey") -> None:
        """Seguro para chamar de qualquer thread (ex.: listener de atalhos)."""
        if self._loop is None:
            logger.error("Interrupção de emergência pedida com o módulo parado")
            return
        self._loop.call_soon_threadsafe(self._schedule, trigger)

    def _schedule(self, trigger: str) -> None:
        if self._running is not None and not self._running.done():
            return  # já em andamento: pedidos repetidos são agrupados
        # Fora do TaskRegistry de propósito: a própria parada não pode ser cancelada por ela.
        self._running = asyncio.create_task(self.trigger(trigger), name="emergency-stop")

    async def trigger(self, trigger: str = "manual") -> EmergencyStopped:
        started = time.perf_counter()
        cancelled, stuck = await self._tasks.cancel_all(timeout=self._cancel_timeout)
        released: list[str] = []
        if self._releaser is not None:
            try:
                released = await asyncio.to_thread(self._releaser.release_all)
            except Exception:
                logger.exception("Falha ao liberar teclado e mouse")
        elapsed_ms = (time.perf_counter() - started) * 1000

        event = EmergencyStopped(
            trigger=trigger,
            cancelled_tasks=cancelled,
            stuck_tasks=stuck,
            released_inputs=tuple(released),
            elapsed_ms=round(elapsed_ms, 1),
        )
        logger.warning(
            "INTERRUPÇÃO DE EMERGÊNCIA (%s): %d tarefa(s) cancelada(s), %d sem resposta, "
            "input liberado: %s, em %.0f ms",
            trigger,
            cancelled,
            stuck,
            ", ".join(released) or "nada",
            elapsed_ms,
        )
        await self._bus.publish(event)
        await self._record(event)
        return event

    async def _record(self, event: EmergencyStopped) -> None:
        if self._activity is None:
            return
        try:
            await self._activity.add(
                ActivityEntry(
                    correlation_id=event.correlation_id,
                    category="safety",
                    action="emergency_stop",
                    status=ActivityStatus.SUCCEEDED
                    if event.stuck_tasks == 0
                    else ActivityStatus.FAILED,
                    detail={
                        "trigger": event.trigger,
                        "cancelled_tasks": event.cancelled_tasks,
                        "stuck_tasks": event.stuck_tasks,
                        "released_inputs": list(event.released_inputs),
                        "elapsed_ms": event.elapsed_ms,
                    },
                )
            )
        except Exception:
            logger.exception("Falha ao registrar a interrupção de emergência no histórico")
