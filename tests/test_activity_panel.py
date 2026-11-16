from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from alfred.app import bootstrap
from alfred.core.task_tracker import mask_sensitive
from alfred.core.tasks import TaskRegistry
from alfred.domain.activity import ActivityEntry, ActivityStatus
from alfred.domain.task_history import StepRecord, TaskRecord
from alfred.domain.tasks import (
    StepStatus,
    TaskFinished,
    TaskStarted,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)
from alfred.infrastructure.db.activity_repository import SqlActivityLogRepository
from alfred.infrastructure.db.task_repository import SqlTaskRepository
from alfred.ui.activity_panel import ActivityPanel, ActivityPanelModule
from alfred.ui.assets import avatar_pixmap, butler_pixmap
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def test_mask_sensitive_hides_secret_fields_and_values() -> None:
    masked = mask_sensitive(
        {
            "app": "Bloco de Notas",
            "password": "hunter2",
            "nested": {"api_key": "x", "text": "Bearer abc.def"},
            "items": ["token=xyz"],
        }
    )
    assert masked["app"] == "Bloco de Notas"
    assert masked["password"] == "***"
    assert masked["nested"]["api_key"] == "***"
    assert "abc.def" not in masked["nested"]["text"]
    assert "xyz" not in masked["items"][0]


async def test_registry_cancels_single_task_by_id() -> None:
    registry = TaskRegistry()
    task_id, other_id = uuid4(), uuid4()
    task = registry.spawn(asyncio.sleep(60), name="a", task_id=task_id)
    other = registry.spawn(asyncio.sleep(60), name="b", task_id=other_id)

    assert registry.is_running(task_id)
    assert registry.cancel(task_id)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not registry.is_running(task_id)
    assert registry.cancel(task_id) is False
    assert not other.done()
    other.cancel()


async def test_tracker_persists_real_lifecycle(tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.database is not None
    await app.manager.start_all()
    try:
        task_id = uuid4()
        await app.bus.publish(TaskStarted(task_id=task_id, title="Escrever nota", origin="ui"))
        await app.bus.publish(
            TaskStepStarted(
                task_id=task_id,
                position=1,
                tool="type_text",
                params={"text": "olá", "password": "segredo"},
            )
        )
        await app.bus.publish(
            TaskStepFinished(
                task_id=task_id, position=1, status=StepStatus.SUCCEEDED, result={"chars": 3}
            )
        )
        await app.bus.publish(
            TaskStepStarted(task_id=task_id, position=2, tool="save_file", params={})
        )
        await app.bus.publish(
            TaskStepFinished(
                task_id=task_id, position=2, status=StepStatus.FAILED, error="disco cheio"
            )
        )
        await app.bus.publish(
            TaskFinished(task_id=task_id, status=TaskStatus.FAILED, error="disco cheio")
        )
        await app.bus.drain(timeout=2)

        (record,) = await SqlTaskRepository(app.database).list_tasks()
        assert record.status is TaskStatus.FAILED
        assert record.error == "disco cheio"
        assert record.completed_steps == 1
        assert [s.status for s in record.steps] == [StepStatus.SUCCEEDED, StepStatus.FAILED]
        assert record.steps[0].params == {"text": "olá", "password": "***"}
        assert record.steps[0].result == {"chars": 3}
        assert record.finished_at is not None
    finally:
        await app.manager.stop_all()


async def test_tasks_left_running_are_marked_interrupted_on_restart(tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    await app.manager.start_all()
    await app.bus.publish(TaskStarted(task_id=uuid4(), title="t", origin="ui"))
    await app.bus.drain(timeout=1)
    await app.manager.stop_all()  # "crash" com a tarefa em execução

    again, _ = bootstrap(tmp_path)
    assert again.database is not None
    await again.manager.start_all()
    try:
        (record,) = await SqlTaskRepository(again.database).list_tasks()
        assert record.status is TaskStatus.CANCELLED
        assert record.error is not None
        assert "encerrado" in record.error
    finally:
        await again.manager.stop_all()


# ------------------------------------------------------------------ painel
def record(status: TaskStatus = TaskStatus.RUNNING) -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id=uuid4(),
        correlation_id=uuid4(),
        title="Abrir bloco de notas",
        status=status,
        origin="voice",
        created_at=now,
        steps=(
            StepRecord(
                position=1,
                tool="open_app",
                status=StepStatus.SUCCEEDED,
                params={"app": "notepad"},
                started_at=now,
                finished_at=now,
            ),
            StepRecord(
                position=2,
                tool="type_text",
                status=StepStatus.FAILED,
                error="janela não encontrada",
                started_at=now,
            ),
        ),
    )


def test_panel_renders_real_tasks_and_details(qt_app: QApplication) -> None:
    panel = ActivityPanel(on_cancel=lambda _: True)
    task = record(TaskStatus.FAILED)
    panel.set_tasks([task], running=set())

    top = panel.tasks_tree.topLevelItem(0)
    assert top.text(0) == "Abrir bloco de notas"
    assert top.text(1) == "Falhou"
    assert top.text(5) == "1"  # etapas concluídas — sem progresso estimado
    assert top.childCount() == 2

    panel.tasks_tree.setCurrentItem(top.child(1))
    assert "janela não encontrada" in panel.detail.toPlainText()
    panel.tasks_tree.setCurrentItem(top.child(0))
    assert '"app": "notepad"' in panel.detail.toPlainText()
    assert not panel.cancel_button.isEnabled()


def test_cancel_only_for_running_tasks(qt_app: QApplication) -> None:
    cancelled: list[object] = []
    panel = ActivityPanel(on_cancel=lambda task_id: cancelled.append(task_id) or True)
    task = record()
    panel.set_tasks([task], running={task.id})
    panel.tasks_tree.setCurrentItem(panel.tasks_tree.topLevelItem(0))

    assert panel.cancel_button.isEnabled()
    QTest.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)
    assert cancelled == [task.id]
    assert "Cancelamento solicitado" in panel.feedback.text()
    assert not panel.cancel_button.isEnabled()


def test_empty_states_and_history(qt_app: QApplication) -> None:
    panel = ActivityPanel(on_cancel=lambda _: True)
    panel.set_tasks([], running=set())
    assert panel.showing_empty_tasks

    panel.set_history(
        [
            ActivityEntry(
                category="safety",
                action="emergency_stop",
                status=ActivityStatus.SUCCEEDED,
                detail={"cancelled_tasks": 2},
            )
        ]
    )
    item = panel.history_tree.topLevelItem(0)
    assert item.text(1) == "safety"
    assert "cancelled_tasks" in item.text(4)


def test_assets_load(qt_app: QApplication) -> None:
    assert butler_pixmap(150).height() == 150
    assert avatar_pixmap(44).width() == 44


def test_module_shows_live_tasks_and_cancels(qt_app: QApplication, tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.database is not None
    assert app.tasks is not None
    module = ActivityPanelModule(
        bus=app.bus,
        tasks=SqlTaskRepository(app.database),
        activity=SqlActivityLogRepository(app.database),
        is_running=app.tasks.is_running,
        cancel=app.tasks.cancel,
    )

    async def scenario() -> tuple[str, bool, bool]:
        assert app.tasks is not None
        await app.manager.start_all()
        await module.start()
        task_id = uuid4()
        running = app.tasks.spawn(asyncio.sleep(60), name="demo", task_id=task_id)
        await app.bus.publish(TaskStarted(task_id=task_id, title="Tarefa real", origin="ui"))
        await app.bus.drain(timeout=1)
        module.show()
        await module.refresh()
        assert module.panel is not None
        top = module.panel.tasks_tree.topLevelItem(0)
        title = top.text(0)
        module.panel.tasks_tree.setCurrentItem(top)
        can_cancel = module.panel.cancel_button.isEnabled()
        module.panel.cancel_button.click()
        await asyncio.sleep(0)
        cancelled = running.cancelled()
        await module.stop()
        await app.manager.stop_all()
        return title, can_cancel, cancelled

    title, can_cancel, cancelled = run_on_qt(scenario())
    assert title == "Tarefa real"
    assert can_cancel
    assert cancelled
