from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from alfred.app import bootstrap
from alfred.core.confirmations import ConfirmationService
from alfred.core.event_bus import EventBus
from alfred.domain.confirmation import (
    ConfirmationDecision,
    ConfirmationRequested,
    ConfirmationResolved,
)
from alfred.domain.emergency import EmergencyStopped
from alfred.infrastructure.db.activity_repository import SqlActivityLogRepository
from alfred.ui.confirmation_panel import ConfirmationPanel
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt

D = ConfirmationDecision
REQUEST: dict[str, Any] = {
    "action": "Excluir arquivo",
    "target": "C:/Users/demo/relatorio.docx",
    "impact": "A exclusão não pode ser desfeita.",
    "category": "files",
}


@pytest.fixture
async def service() -> AsyncIterator[tuple[ConfirmationService, EventBus, list[Any]]]:
    bus = EventBus()
    await bus.start()
    events: list[Any] = []

    async def record(event: Any) -> None:
        events.append(event)

    bus.subscribe(ConfirmationRequested, record)
    bus.subscribe(ConfirmationResolved, record)
    svc = ConfirmationService(bus)
    await svc.start()
    yield svc, bus, events
    await svc.stop()
    await bus.stop()


async def answer_when_requested(svc: ConfirmationService, decision: ConfirmationDecision) -> None:
    while not svc.pending:
        await asyncio.sleep(0.005)
    svc.resolve(svc.pending[0], decision)


@pytest.mark.parametrize("decision", [D.APPROVED, D.DENIED])
async def test_user_decision_is_returned(
    service: tuple[ConfirmationService, EventBus, list[Any]], decision: ConfirmationDecision
) -> None:
    svc, bus, events = service
    answer = asyncio.create_task(answer_when_requested(svc, decision))

    assert await svc.request(**REQUEST, timeout=2) is decision
    await answer
    await bus.drain(timeout=1)
    requested, resolved = events
    assert isinstance(requested, ConfirmationRequested)
    assert isinstance(resolved, ConfirmationResolved)
    assert resolved.request_id == requested.request_id
    assert resolved.decision is decision
    assert svc.pending == ()


async def test_no_answer_is_a_denial(
    service: tuple[ConfirmationService, EventBus, list[Any]],
) -> None:
    svc, _, _ = service
    assert await svc.request(**REQUEST, timeout=0.05) is D.TIMEOUT


async def test_late_answer_is_ignored(
    service: tuple[ConfirmationService, EventBus, list[Any]],
) -> None:
    svc, bus, events = service
    await svc.request(**REQUEST, timeout=0.05)
    await bus.drain(timeout=1)
    assert svc.resolve(events[0].request_id, D.APPROVED) is False


async def test_emergency_denies_pending_requests(
    service: tuple[ConfirmationService, EventBus, list[Any]],
) -> None:
    svc, bus, _ = service

    async def emergency() -> None:
        while not svc.pending:
            await asyncio.sleep(0.005)
        await bus.publish(
            EmergencyStopped(
                trigger="hotkey",
                cancelled_tasks=0,
                stuck_tasks=0,
                released_inputs=(),
                elapsed_ms=1,
            )
        )

    trigger = asyncio.create_task(emergency())
    assert await svc.request(**REQUEST, timeout=5) is D.DENIED
    await trigger


async def test_cancelled_request_publishes_denial(
    service: tuple[ConfirmationService, EventBus, list[Any]],
) -> None:
    svc, bus, events = service
    task = asyncio.create_task(svc.request(**REQUEST, timeout=5))
    while not svc.pending:
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await bus.drain(timeout=1)

    assert events[-1].decision is D.DENIED
    assert svc.pending == ()


async def test_decisions_are_recorded_in_history(tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.confirmations is not None
    assert app.database is not None
    await app.manager.start_all()
    try:
        await app.confirmations.request(**REQUEST, timeout=0.05)
        (entry,) = [
            e
            for e in await SqlActivityLogRepository(app.database).list()
            if e.category == "confirmation"
        ]
        assert entry.action == "files"
        assert entry.status == "denied"
        assert entry.detail["decision"] == "timeout"
        assert entry.detail["target"] == REQUEST["target"]
    finally:
        await app.manager.stop_all()


# ------------------------------------------------------------------ painel
@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def make_request(action: str = "Excluir arquivo", timeout: float = 30) -> ConfirmationRequested:
    return ConfirmationRequested(**{**REQUEST, "action": action}, timeout_seconds=timeout)


class RecordingService:
    def __init__(self) -> None:
        self.answers: list[tuple[Any, ConfirmationDecision]] = []

    def resolve(self, request_id: Any, decision: ConfirmationDecision) -> bool:
        self.answers.append((request_id, decision))
        return True


def test_panel_shows_request_and_defaults_to_deny(qt_app: QApplication) -> None:
    svc = RecordingService()
    panel = ConfirmationPanel(svc)  # type: ignore[arg-type]
    request = make_request(timeout=12)

    panel.enqueue(request)

    assert panel.isVisible()
    assert panel.action.text() == "Excluir arquivo"
    assert "relatorio.docx" in panel.target.text()
    assert "12 s" in panel.countdown.text()
    assert not panel.approve.autoDefault()
    QTest.keyClick(panel, Qt.Key.Key_Escape)
    assert svc.answers == [(request.request_id, D.DENIED)]
    assert not panel.isVisible()


def test_panel_queues_requests_and_handles_external_resolution(qt_app: QApplication) -> None:
    svc = RecordingService()
    panel = ConfirmationPanel(svc)  # type: ignore[arg-type]
    first, second, third = make_request("A"), make_request("B"), make_request("C")
    for request in (first, second, third):
        panel.enqueue(request)

    assert panel.action.text() == "A"
    assert "+2" in panel.queued.text()

    QTest.mouseClick(panel.approve, Qt.MouseButton.LeftButton)
    assert svc.answers == [(first.request_id, D.APPROVED)]
    assert panel.action.text() == "B"

    panel.discard(second.request_id)  # prazo esgotado no serviço
    assert panel.action.text() == "C"
    panel.discard(third.request_id)
    assert not panel.isVisible()


def test_full_flow_approval_through_ui(qt_app: QApplication, tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path, with_ui=False)
    assert app.confirmations is not None
    from alfred.ui.confirmation_panel import ConfirmationUiModule

    ui = ConfirmationUiModule(bus=app.bus, service=app.confirmations)

    async def scenario() -> ConfirmationDecision:
        await app.manager.start_all()
        await ui.start()
        assert app.confirmations is not None
        pending = asyncio.ensure_future(app.confirmations.request(**REQUEST, timeout=5))
        while ui.panel is None or ui.panel.current is None:
            await asyncio.sleep(0.01)
        QTest.mouseClick(ui.panel.approve, Qt.MouseButton.LeftButton)
        decision = await pending
        await ui.stop()
        await app.manager.stop_all()
        return decision

    assert run_on_qt(scenario()) is D.APPROVED
