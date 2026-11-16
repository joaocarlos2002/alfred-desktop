from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from alfred.core.assistant_state import AssistantStateService
from alfred.core.event_bus import EventBus
from alfred.domain.assistant import (
    AssistantState,
    AssistantStateChanged,
    AssistantStateMachine,
    ListeningStarted,
    ListeningStopped,
)
from alfred.domain.confirmation import ConfirmationDecision as Decision
from alfred.domain.confirmation import ConfirmationRequested, ConfirmationResolved
from alfred.domain.emergency import EmergencyStopped
from alfred.domain.tasks import (
    StepStatus,
    TaskFinished,
    TaskStarted,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)

S = AssistantState


def confirmation() -> ConfirmationRequested:
    return ConfirmationRequested(
        action="Excluir arquivo",
        target="C:/x.txt",
        impact="irreversível",
        category="files",
        timeout_seconds=30,
    )


def test_full_task_lifecycle() -> None:
    machine = AssistantStateMachine()
    task = uuid4()

    assert machine.apply(ListeningStarted()) is S.LISTENING
    assert machine.apply(ListeningStopped()) is S.IDLE
    assert machine.apply(TaskStarted(task_id=task, title="Abrir bloco", origin="voice")) is (
        S.PROCESSING
    )
    assert machine.apply(TaskStepStarted(task_id=task, position=1, tool="open_app")) is S.EXECUTING
    assert machine.detail == "open_app"
    assert (
        machine.apply(TaskStepFinished(task_id=task, position=1, status=StepStatus.SUCCEEDED))
        is S.PROCESSING
    )
    assert machine.apply(TaskFinished(task_id=task, status=TaskStatus.SUCCEEDED)) is S.DONE
    assert machine.detail == "Abrir bloco"
    assert machine.settle() is S.IDLE


def test_failure_shows_error_with_reason() -> None:
    machine = AssistantStateMachine()
    task = uuid4()
    machine.apply(TaskStarted(task_id=task, title="t", origin="ui"))

    assert (
        machine.apply(TaskFinished(task_id=task, status=TaskStatus.FAILED, error="sem janela"))
        is S.ERROR
    )
    assert machine.detail == "sem janela"


def test_confirmation_has_priority_over_execution() -> None:
    machine = AssistantStateMachine()
    task = uuid4()
    machine.apply(TaskStarted(task_id=task, title="t", origin="ui"))
    machine.apply(TaskStepStarted(task_id=task, position=1, tool="delete_file"))
    request = confirmation()

    assert machine.apply(request) is S.AWAITING_CONFIRMATION
    assert machine.apply(ListeningStarted()) is None  # continua aguardando confirmação
    assert (
        machine.apply(ConfirmationResolved(request_id=request.request_id, decision=Decision.DENIED))
        is S.LISTENING
    )


def test_cancelled_task_returns_to_idle_without_done() -> None:
    machine = AssistantStateMachine()
    task = uuid4()
    machine.apply(TaskStarted(task_id=task, title="t", origin="ui"))
    assert machine.apply(TaskFinished(task_id=task, status=TaskStatus.CANCELLED)) is S.IDLE


def test_other_running_task_keeps_processing_after_one_finishes() -> None:
    machine = AssistantStateMachine()
    a, b = uuid4(), uuid4()
    machine.apply(TaskStarted(task_id=a, title="a", origin="ui"))
    machine.apply(TaskStarted(task_id=b, title="b", origin="ui"))

    assert machine.apply(TaskFinished(task_id=a, status=TaskStatus.SUCCEEDED)) is None
    assert machine.state is S.PROCESSING


def test_emergency_resets_everything() -> None:
    machine = AssistantStateMachine()
    machine.apply(TaskStarted(task_id=uuid4(), title="t", origin="ui"))
    machine.apply(confirmation())

    stopped = EmergencyStopped(
        trigger="hotkey", cancelled_tasks=1, stuck_tasks=0, released_inputs=(), elapsed_ms=1
    )
    assert machine.apply(stopped) is S.IDLE


def test_unrelated_events_are_ignored() -> None:
    assert (
        AssistantStateMachine().apply(
            ConfirmationResolved(request_id=uuid4(), decision=Decision.APPROVED)
        )
        is None
    )


@pytest.fixture
async def bus() -> EventBus:
    bus = EventBus()
    await bus.start()
    return bus


async def test_service_publishes_changes_and_settles(bus: EventBus) -> None:
    service = AssistantStateService(bus, settle_after=0.05)
    await service.start()
    states: list[AssistantState] = []

    async def record(event: AssistantStateChanged) -> None:
        states.append(event.state)

    bus.subscribe(AssistantStateChanged, record)
    task = uuid4()
    await bus.publish(TaskStarted(task_id=task, title="t", origin="ui"))
    await bus.publish(TaskFinished(task_id=task, status=TaskStatus.SUCCEEDED))
    await bus.drain(timeout=1)
    await asyncio.sleep(0.15)
    await bus.drain(timeout=1)

    assert states == [S.PROCESSING, S.DONE, S.IDLE]
    assert service.state is S.IDLE
    await service.stop()
    await bus.stop()
