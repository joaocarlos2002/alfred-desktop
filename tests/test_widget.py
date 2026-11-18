from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from alfred.app import bootstrap
from alfred.domain.assistant import AssistantState, AssistantStateChanged
from alfred.domain.voice import SpeechFinished, SpeechStarted
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
from alfred.ui.quick_panel import QuickPanel
from alfred.ui.visual_state import VisualMode, visual_mode
from alfred.ui.widget import (
    EDGE_MARGIN,
    FloatingWidget,
    PanelController,
    WidgetModule,
    initial_position,
)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def test_window_is_floating_frameless_and_not_in_taskbar(qt_app: QApplication) -> None:
    flags = FloatingWidget().windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool


@pytest.mark.parametrize(
    ("state", "speaking", "expected"),
    [
        (AssistantState.IDLE, False, VisualMode.IDLE),
        (AssistantState.LISTENING, False, VisualMode.LISTENING),
        (AssistantState.PROCESSING, False, VisualMode.THINKING),
        (AssistantState.EXECUTING, False, VisualMode.EXECUTING),
        (AssistantState.AWAITING_CONFIRMATION, False, VisualMode.CONFIRM),
        (AssistantState.DONE, False, VisualMode.DONE),
        (AssistantState.ERROR, False, VisualMode.ERROR),
        (AssistantState.IDLE, True, VisualMode.SPEAKING),
        (AssistantState.PROCESSING, True, VisualMode.SPEAKING),
        (AssistantState.LISTENING, True, VisualMode.LISTENING),  # ouvir tem prioridade
        (AssistantState.AWAITING_CONFIRMATION, True, VisualMode.CONFIRM),
    ],
)
def test_visual_mode_follows_real_state(
    state: AssistantState, speaking: bool, expected: VisualMode
) -> None:
    assert visual_mode(state, speaking=speaking) is expected


@pytest.mark.parametrize("state", list(AssistantState))
def test_every_state_paints_and_animates(qt_app: QApplication, state: AssistantState) -> None:
    widget = FloatingWidget()
    widget.show()
    widget.set_state(state, "detalhe")
    for _ in range(5):
        widget._tick()
    assert widget.animating
    assert "detalhe" in widget.toolTip()
    widget.grab()  # pinta sem erro em todos os estados
    widget.set_speaking(True)
    widget.grab()
    widget.close()


def test_animations_stop_when_disabled_or_hidden(qt_app: QApplication) -> None:
    widget = FloatingWidget()
    assert not widget.animating  # nada roda com a janela escondida
    widget.show()
    assert widget.animating
    widget.set_animations(False)
    assert not widget.animating
    widget.set_state(AssistantState.LISTENING)
    assert not widget.animating
    widget.set_animations(True)
    assert widget.animating
    widget.hide()
    assert not widget.animating


def test_drag_emits_moved_and_click_emits_clicked(qt_app: QApplication) -> None:
    widget = FloatingWidget()
    widget.move(100, 100)
    widget.show()
    moves: list[tuple[int, int]] = []
    clicks: list[bool] = []
    drags: list[bool] = []
    widget.moved.connect(lambda x, y: moves.append((x, y)))
    widget.clicked.connect(lambda: clicks.append(True))
    widget.drag_started.connect(lambda: drags.append(True))

    center = widget.rect().center()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=center)
    assert clicks == [True]

    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))  # halo, fora do orbe
    assert clicks == [True]

    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(widget, center + QPoint(40, 25))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=center + QPoint(40, 25))
    assert len(moves) == 1
    assert drags == [True]
    assert clicks == [True]
    widget.close()


def test_only_the_orb_circle_counts_as_hover(qt_app: QApplication) -> None:
    widget = FloatingWidget()
    widget.move(200, 200)
    widget.show()
    center = widget.orb_center_global()
    assert widget.contains_global(center)
    assert widget.contains_global(center + QPoint(FloatingWidget.ORB // 2, 0))
    assert not widget.contains_global(center + QPoint(FloatingWidget.ORB, 0))
    widget.close()


def test_initial_position_falls_back_to_the_corner(qt_app: QApplication) -> None:
    screen = QGuiApplication.primaryScreen().availableGeometry()
    inside = (screen.left() + 10, screen.top() + 10)

    assert initial_position(inside, 56) == QPoint(*inside)
    size, orb = FloatingWidget.SIZE, FloatingWidget.ORB
    fallback = initial_position((-99999, -99999), size, orb)
    orb_right = fallback.x() + (size + orb) // 2
    assert screen.right() - orb_right == pytest.approx(EDGE_MARGIN, abs=2)  # no cantinho


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def make_controller() -> tuple[FloatingWidget, QuickPanel, PanelController, list[QPoint], Clock]:
    widget = FloatingWidget()
    screen = QGuiApplication.primaryScreen().availableGeometry()
    widget.move(initial_position(None, widget.SIZE, widget.ORB))
    widget.show()
    clock = Clock()
    cursor = [widget.orb_center_global()]
    panel = QuickPanel(on_change=lambda *_: None, on_open_settings=lambda _: None, clock=clock)
    controller = PanelController(widget, panel, cursor=lambda: cursor[0], clock=clock)
    assert screen.contains(widget.orb_center_global())
    return widget, panel, controller, cursor, clock


def test_hover_opens_and_leaving_closes(qt_app: QApplication) -> None:
    widget, panel, controller, cursor, clock = make_controller()
    widget.hovered.emit()
    controller._open_if_still_hovering()  # o atraso de intenção passou
    assert panel.is_open
    panel.finish_animations()

    cursor[0] = panel.mapToGlobal(panel.rect().center())  # dentro do painel: continua aberto
    controller.check_cursor()
    clock.now += 5
    controller.check_cursor()
    assert panel.is_open

    cursor[0] = QPoint(-5000, -5000)
    controller.check_cursor()
    clock.now += 0.2
    controller.check_cursor()
    assert panel.is_open  # ainda dentro da tolerância
    clock.now += 0.3
    controller.check_cursor()
    assert not panel.is_open
    panel.finish_animations()
    assert not panel.isVisible()
    widget.close()


def test_hover_that_moves_away_before_the_delay_does_not_open(qt_app: QApplication) -> None:
    widget, panel, controller, cursor, _ = make_controller()
    widget.hovered.emit()
    cursor[0] = QPoint(-5000, -5000)
    controller._open_if_still_hovering()
    assert not panel.is_open
    widget.close()


def test_click_pins_and_second_click_closes(qt_app: QApplication) -> None:
    widget, panel, controller, cursor, clock = make_controller()
    widget.clicked.emit()
    assert panel.is_open
    assert controller.pinned
    cursor[0] = QPoint(-5000, -5000)
    clock.now += 10
    controller.check_cursor()
    controller.check_cursor()
    assert panel.is_open  # fixado: sair com o mouse não fecha
    widget.clicked.emit()
    assert not panel.is_open
    assert not controller.pinned
    widget.close()


def test_dragging_or_choosing_an_action_closes_the_panel(qt_app: QApplication) -> None:
    widget, panel, controller, _, _ = make_controller()
    controller.open()
    widget.drag_started.emit()
    assert not panel.is_open
    controller.open()
    panel.all_settings.click()
    assert not panel.is_open
    widget.close()


def test_module_follows_real_state_speech_and_settings(
    qt_app: QApplication, tmp_path: Path
) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.settings is not None
    opened: list[Any] = []
    module = WidgetModule(
        bus=app.bus,
        settings=app.settings,
        on_open_settings=opened.append,
        on_open_activity=lambda: opened.append("activity"),
        voice_available=True,
    )

    async def scenario() -> dict[str, Any]:
        await app.manager.start_all()
        await module.start()
        assert module.widget is not None
        assert module.panel is not None
        result: dict[str, Any] = {}
        await app.bus.publish(AssistantStateChanged(state=AssistantState.EXECUTING))
        await app.bus.drain(timeout=1)
        result["state"] = module.widget.state
        await app.bus.publish(SpeechStarted(text="Pois não."))
        await app.bus.drain(timeout=1)
        result["speaking"] = (module.widget.mode, module.panel.mode)
        await app.bus.publish(SpeechFinished(text="Pois não.", interrupted=False))
        await app.bus.drain(timeout=1)
        result["after"] = module.widget.mode

        module.panel.autostart.click()  # interruptor do painel altera a configuração real
        await asyncio.sleep(0.05)
        await app.bus.drain(timeout=1)
        assert app.settings is not None
        result["autostart"] = app.settings.current.general.start_with_windows
        await app.settings.update("appearance", animations=False)
        await app.bus.drain(timeout=1)
        result["animations_chip"] = module.panel.animations.isChecked()

        module.panel.tiles[1].click()
        module.panel.tiles[5].click()

        module.widget.moved.emit(321, 123)
        await asyncio.sleep(0.05)
        await app.settings.update("appearance", widget_visible=False)
        await app.bus.drain(timeout=1)
        result["visible"] = module.widget.isVisible()
        await module.stop()
        await app.manager.stop_all()
        result["position"] = app.settings.current.appearance.widget_position
        return result

    result = run_on_qt(scenario())
    assert result["state"] is AssistantState.EXECUTING
    assert result["speaking"] == (VisualMode.SPEAKING, VisualMode.SPEAKING)
    assert result["after"] is VisualMode.EXECUTING
    assert result["autostart"] is True
    assert result["animations_chip"] is False
    assert opened == ["ai", "activity"]
    assert result["position"] == (321, 123)
    assert not result["visible"]
