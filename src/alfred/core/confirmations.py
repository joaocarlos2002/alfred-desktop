"""Confirmação explícita de ações sensíveis (seção 6). Sem resposta, a ação é recusada."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.activity import ActivityEntry, ActivityLogRepository, ActivityStatus
from alfred.domain.confirmation import (
    ConfirmationDecision,
    ConfirmationRequested,
    ConfirmationResolved,
)
from alfred.domain.emergency import EmergencyStopped
from alfred.domain.module import Module

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0

_ACTIVITY_STATUS = {
    ConfirmationDecision.APPROVED: ActivityStatus.SUCCEEDED,
    ConfirmationDecision.DENIED: ActivityStatus.DENIED,
    ConfirmationDecision.TIMEOUT: ActivityStatus.DENIED,
}


class ConfirmationService(Module):
    name = "confirmations"

    def __init__(self, bus: EventBus, *, activity: ActivityLogRepository | None = None) -> None:
        self.depends_on = ("event_bus", "database") if activity else ("event_bus",)
        self._bus = bus
        self._activity = activity
        self._pending: dict[UUID, asyncio.Future[ConfirmationDecision]] = {}
        self._subscription: Subscription[EmergencyStopped] | None = None

    @property
    def pending(self) -> tuple[UUID, ...]:
        return tuple(self._pending)

    async def start(self) -> None:
        self._subscription = self._bus.subscribe(
            EmergencyStopped, self._on_emergency, name="confirmations"
        )

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        for request_id in list(self._pending):
            self.resolve(request_id, ConfirmationDecision.DENIED)

    async def request(
        self,
        *,
        action: str,
        target: str,
        impact: str,
        category: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> ConfirmationDecision:
        """Pede confirmação e aguarda. Prazo esgotado ou cancelamento contam como recusa."""
        request = ConfirmationRequested(
            action=action,
            target=target,
            impact=impact,
            category=category,
            timeout_seconds=timeout,
        )
        future: asyncio.Future[ConfirmationDecision] = asyncio.get_running_loop().create_future()
        self._pending[request.request_id] = future
        await self._bus.publish(request)
        decision = ConfirmationDecision.DENIED
        try:
            decision = await asyncio.wait_for(asyncio.shield(future), timeout)
        except TimeoutError:
            decision = ConfirmationDecision.TIMEOUT
        finally:
            self._pending.pop(request.request_id, None)
            await self._finish(request, decision)
        return decision

    def resolve(self, request_id: UUID, decision: ConfirmationDecision) -> bool:
        """Registra a resposta do usuário. Devolve ``False`` se o pedido não está mais pendente."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    async def _on_emergency(self, event: EmergencyStopped) -> None:
        for request_id in list(self._pending):
            self.resolve(request_id, ConfirmationDecision.DENIED)

    async def _finish(self, request: ConfirmationRequested, decision: ConfirmationDecision) -> None:
        logger.info("Confirmação '%s' (%s): %s", request.action, request.category, decision.value)
        await self._bus.publish(
            ConfirmationResolved(
                request_id=request.request_id,
                decision=decision,
                correlation_id=request.correlation_id,
            )
        )
        if self._activity is None:
            return
        try:
            await self._activity.add(
                ActivityEntry(
                    correlation_id=request.correlation_id,
                    category="confirmation",
                    action=request.category,
                    status=_ACTIVITY_STATUS[decision],
                    detail={
                        "request_id": str(request.request_id),
                        "action": request.action,
                        "target": request.target,
                        "decision": decision.value,
                    },
                )
            )
        except Exception:
            logger.exception("Falha ao registrar a confirmação no histórico")
