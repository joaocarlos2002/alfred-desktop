from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from alfred.config.settings import Settings
from alfred.ui.motion import MOTION
from alfred.ui.qt_runtime import ensure_qt_application
from alfred.ui.quick_panel import ANCHOR_INSET, QuickPanel, format_hotkey, greeting
from alfred.ui.visual_state import VisualMode, out_back, out_expo


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self) -> None:
        self.changes: list[tuple[str, dict[str, Any]]] = []
        self.opened: list[Any] = []

    def change(self, section: str, changes: dict[str, Any]) -> None:
        self.changes.append((section, changes))


def make_panel(
    *, voice: bool = True, providers: dict[str, bool] | None = None
) -> tuple[QuickPanel, Recorder, Clock]:
    recorder = Recorder()
    clock = Clock()
    panel = QuickPanel(
        on_change=recorder.change,
        on_open_settings=recorder.opened.append,
        on_open_activity=lambda: recorder.opened.append("activity"),
        providers=lambda: providers or {},
        voice_available=voice,
        clock=clock,
        cursor=lambda: QPoint(0, 0),
    )
    return panel, recorder, clock


def corner() -> QPoint:
    area = QGuiApplication.primaryScreen().availableGeometry()
    return QPoint(area.right() - 50, area.bottom() - 50)


def test_greeting_and_hotkey_text() -> None:
    assert greeting(8) == "Bom dia."
    assert greeting(14) == "Boa tarde."
    assert greeting(22) == "Boa noite."
    assert greeting(3) == "Boa noite."
    assert format_hotkey("ctrl+alt+space") == "Ctrl + Alt + Espaço"
    assert format_hotkey("ctrl+shift+a") == "Ctrl + Shift + A"


def test_easing_curves_end_where_they_should() -> None:
    assert out_expo(0) == pytest.approx(0, abs=1e-3)
    assert out_expo(1) == 1
    assert out_back(1) == pytest.approx(1)
    assert max(out_back(x / 100) for x in range(101)) > 1  # mola passa do alvo


def test_panel_shows_real_settings(qt_app: QApplication) -> None:
    panel, _, _ = make_panel(providers={"ollama": True})
    settings = Settings().with_section("voice", enabled=True, wake_word_enabled=False)
    settings = settings.with_section("general", start_with_windows=True)
    settings = settings.with_section("ai", model="qwen2.5:7b")
    panel.load(settings)

    assert panel.mic.isChecked()
    assert not panel.wake.isChecked()
    assert panel.animations.isChecked()
    assert panel.autostart.isChecked()
    assert panel.ai.name.text() == "qwen2.5:7b"
    assert "local" in panel.ai.caption.text()
    assert "Ollama" in panel.ai.caption.text()
    assert "Ctrl + Alt + Espaço" in panel.subtitle.text()
    assert "Alfred" not in panel.subtitle.text()  # palavra de ativação desligada


def test_external_provider_is_labelled_as_such(qt_app: QApplication) -> None:
    panel, _, _ = make_panel(providers={"openai_compat": False})
    panel.load(Settings().with_section("ai", provider="openai_compat", model=None))
    assert "externa" in panel.ai.caption.text()
    assert panel.ai.name.text() == "modelo padrão do provedor"


def test_toggles_change_the_right_settings(qt_app: QApplication) -> None:
    panel, recorder, _ = make_panel()
    panel.load(Settings())
    panel.mic.click()
    panel.wake.click()  # estava ligado (padrão) mas o microfone não: liga os dois
    panel.animations.click()
    panel.autostart.click()
    assert recorder.changes == [
        ("voice", {"enabled": True}),
        ("voice", {"enabled": True, "wake_word_enabled": True}),
        ("appearance", {"animations": False}),
        ("general", {"start_with_windows": True}),
    ]
    panel.load(Settings().with_section("voice", enabled=True))
    recorder.changes.clear()
    panel.wake.click()
    assert recorder.changes == [("voice", {"wake_word_enabled": False})]


def test_voice_toggles_are_disabled_without_a_microphone(qt_app: QApplication) -> None:
    panel, _, _ = make_panel(voice=False)
    panel.load(Settings())
    assert not panel.mic.isEnabled()
    assert not panel.wake.isEnabled()
    assert "microfone" in panel.mic.toolTip()
    assert panel.animations.isEnabled()


def test_shortcuts_open_sections_and_close_the_panel(qt_app: QApplication) -> None:
    panel, recorder, _ = make_panel()
    actions: list[bool] = []
    panel.action_taken.connect(lambda: actions.append(True))
    for tile in panel.tiles:
        tile.click()
    panel.ai.change.click()
    panel.all_settings.click()
    assert recorder.opened == [
        "voice",
        "ai",
        "triggers",
        "memory",
        "privacy",
        "activity",
        "ai",
        None,
    ]
    assert len(actions) == 8


def test_history_falls_back_to_settings_without_activity_panel(qt_app: QApplication) -> None:
    opened: list[Any] = []
    panel = QuickPanel(on_change=lambda *_: None, on_open_settings=opened.append)
    panel.tiles[-1].click()
    assert opened == ["history"]


def test_panel_grows_from_the_orb_toward_free_space(qt_app: QApplication) -> None:
    panel, _, _ = make_panel()
    area = QGuiApplication.primaryScreen().availableGeometry()

    orb = corner()
    panel.place(orb)
    assert panel.geometry().right() == orb.x() + ANCHOR_INSET - 1
    assert panel.geometry().bottom() == orb.y() + ANCHOR_INSET - 1
    assert area.contains(panel.geometry())
    assert not panel.orb_slot_right.isHidden()

    top_left = QPoint(area.left() + 40, area.top() + 40)
    panel.place(top_left)
    assert panel.geometry().left() >= area.left()
    assert panel.geometry().top() >= area.top()
    assert panel.anchor.x() < panel.width() / 2
    assert panel.anchor.y() < panel.height() / 2


def test_animated_open_and_close(qt_app: QApplication) -> None:
    panel, _, clock = make_panel()
    panel.set_mode(VisualMode.LISTENING)
    panel.open_at(corner())
    assert panel.phase == "opening"
    assert panel.isVisible()
    assert panel._sparks  # faíscas ao abrir
    assert panel.title.text().endswith(".")
    clock.now += 0.3
    panel._tick()
    assert panel.phase == "opening"
    panel.grab()
    clock.now += 2
    panel._tick()
    assert panel.phase == "open"
    assert all(effect.progress == 1 for effect in panel._rising)
    assert panel.status.label == "Ouvindo"
    panel.grab()

    panel.close_animated()
    assert panel.phase == "closing"
    assert not panel.is_open
    clock.now += 1
    panel._tick()
    assert panel.phase == "hidden"
    assert not panel.isVisible()


def test_reopening_while_closing_continues_from_where_it_was(qt_app: QApplication) -> None:
    panel, _, clock = make_panel()
    panel.open_at(corner())
    panel.finish_animations()
    panel.close_animated()
    clock.now += 0.2
    panel._tick()
    partial = panel._reveal
    assert 0 < partial < 1
    panel.open_at(corner())
    assert panel.phase == "opening"
    assert panel._reveal == pytest.approx(partial, abs=0.05)
    panel.close_animated()
    clock.now += 1
    panel._tick()


@pytest.fixture
def reduced_motion() -> Iterator[None]:
    MOTION.configure(animations_enabled=False)
    yield
    MOTION.configure(animations_enabled=True)


def test_reduced_motion_opens_and_closes_instantly(
    qt_app: QApplication, reduced_motion: None
) -> None:
    panel, _, _ = make_panel()
    panel.open_at(corner())
    assert panel.phase == "open"
    assert panel.isVisible()
    assert not panel._sparks
    assert all(effect.progress == 1 for effect in panel._rising)
    panel.close_animated()
    assert panel.phase == "hidden"
    assert not panel.isVisible()


def test_contains_global_respects_rounded_corners(qt_app: QApplication) -> None:
    panel, _, _ = make_panel()
    panel.open_at(corner())
    panel.finish_animations()
    rect = panel.geometry()
    assert panel.contains_global(rect.center())
    assert not panel.contains_global(rect.topLeft())  # canto arredondado
    panel.close_animated()
    panel.finish_animations()
    assert not panel.contains_global(rect.center())
