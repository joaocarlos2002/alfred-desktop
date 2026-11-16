"""Fonte única do estado visível do assistente: eventos reais → ``AssistantStateChanged``."""

from __future__ import annotations

import asyncio
import logging

from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.assistant import (
    TRANSIENT_STATES,
    AssistantState,
    AssistantStateChanged,
    AssistantStateMachine,
)
from alfred.domain.events import Event
from alfred.domain.module import Module

logger = logging.getLogger(__name__)


class AssistantStateService(Module):
    name = "assistant_state"
    depends_on = ("event_bus",)

    def __init__(self, bus: EventBus, *, settle_after: float = 2.5) -> None:
        self._bus = bus
        self._settle_after = settle_after
        self._machine = AssistantStateMachine()
        self._settle_task: asyncio.Task[None] | None = None
        self._subscription: Subscription[Event] | None = None

    @property
    def state(self) -> AssistantState:
        return self._machine.state

    async def start(self) -> None:
        self._subscription = self._bus.subscribe(Event, self._on_event, name="assistant-state")

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        self._cancel_settle()

    async def _on_event(self, event: Event) -> None:
        if isinstance(event, AssistantStateChanged):
            return
        changed = self._machine.apply(event)
        if changed is not None:
            await self._publish(changed)

    async def _publish(self, state: AssistantState) -> None:
        self._cancel_settle()
        if state in TRANSIENT_STATES:
            self._settle_task = asyncio.create_task(self._settle_later(), name="assistant-settle")
        logger.debug("Estado do assistente: %s", state)
        await self._bus.publish(AssistantStateChanged(state=state, detail=self._machine.detail))

    async def _settle_later(self) -> None:
        await asyncio.sleep(self._settle_after)
        self._settle_task = None
        changed = self._machine.settle()
        if changed is not None:
            await self._publish(changed)

    def _cancel_settle(self) -> None:
        if self._settle_task is not None and self._settle_task is not asyncio.current_task():
            self._settle_task.cancel()
        self._settle_task = None
