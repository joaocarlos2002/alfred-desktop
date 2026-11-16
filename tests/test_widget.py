from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from alfred.app import bootstrap
from alfred.domain.assistant import AssistantState, AssistantStateChanged
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
from alfred.ui.widget import PULSING, SPINNING, FloatingWidget, WidgetModule, initial_position


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def test_window_is_floating_frameless_and_not_in_taskbar(qt_app: QApplication) -> None:
    flags = FloatingWidget().windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool


@pytest.mark.parametrize("state", list(AssistantState))
def test_animation_runs_only_for_active_states(qt_app: QApplication, state: AssistantState) -> None:
    widget = FloatingWidget()
    widget.set_state(state, "detalhe")

    assert widget.animating == (state in SPINNING | PULSING)
    assert "detalhe" in widget.toolTip()
    widget.grab()  # pinta sem erro em todos os estados


def test_idle_has_no_running_timer_and_animations_can_be_disabled(qt_app: QApplication) -> None:
    widget = FloatingWidget()
    widget.set_state(AssistantState.EXECUTING)
    assert widget.animating
    widget.set_state(AssistantState.IDLE)
    assert not widget.animating

    widget.set_animations(False)
    widget.set_state(AssistantState.LISTENING)
    assert not widget.animating


def test_drag_emits_moved_and_click_emits_clicked(qt_app: QApplication) -> None:
    widget = FloatingWidget()
    widget.move(100, 100)
    widget.show()
    moves: list[tuple[int, int]] = []
    clicks: list[bool] = []
    widget.moved.connect(lambda x, y: moves.append((x, y)))
    widget.clicked.connect(lambda: clicks.append(True))

    center = widget.rect().center()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=center)
    assert clicks == [True]

    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(widget, center + QPoint(40, 25))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=center + QPoint(40, 25))
    assert len(moves) == 1
    assert clicks == [True]
    widget.close()


def test_initial_position_falls_back_when_saved_screen_is_gone(qt_app: QApplication) -> None:
    screen = QGuiApplication.primaryScreen().availableGeometry()
    inside = (screen.left() + 10, screen.top() + 10)

    assert initial_position(inside, 56) == QPoint(*inside)
    fallback = initial_position((-99999, -99999), 56)
    assert screen.contains(fallback)


def test_module_follows_real_state_and_persists_position(
    qt_app: QApplication, tmp_path: Path
) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.settings is not None
    module = WidgetModule(bus=app.bus, settings=app.settings)

    async def scenario() -> tuple[AssistantState, tuple[int, int] | None, bool]:
        await app.manager.start_all()
        await module.start()
        assert module.widget is not None
        await app.bus.publish(AssistantStateChanged(state=AssistantState.EXECUTING))
        await app.bus.drain(timeout=1)
        state = module.widget.state

        module.widget.moved.emit(321, 123)
        await asyncio.sleep(0.05)
        await app.settings.update("appearance", widget_visible=False)
        await app.bus.drain(timeout=1)
        visible = module.widget.isVisible()
        await module.stop()
        await app.manager.stop_all()
        assert app.settings is not None
        return state, app.settings.current.appearance.widget_position, visible

    state, position, visible = run_on_qt(scenario())
    assert state is AssistantState.EXECUTING
    assert position == (321, 123)
    assert not visible
