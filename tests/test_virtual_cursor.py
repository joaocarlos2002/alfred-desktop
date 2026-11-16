from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from alfred.app import bootstrap
from alfred.config.settings import CursorSettings
from alfred.domain.screen import ScreenRect
from alfred.ui.overlay import OverlayAnnotator, OverlayWindow
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
from alfred.ui.virtual_cursor import MAX_MOVE_MS, MIN_MOVE_MS, OverlayVirtualCursor


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


@pytest.fixture
def cursor(qt_app: QApplication) -> OverlayVirtualCursor:
    overlay = OverlayWindow()
    return OverlayVirtualCursor(overlay, OverlayAnnotator(overlay), CursorSettings())


def wait_until(app: QApplication, predicate: Callable[[], bool], timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
    assert predicate()


def test_hidden_until_used(cursor: OverlayVirtualCursor) -> None:
    assert not cursor.layer.active
    assert not cursor.layer.animating


def test_moves_smoothly_to_target(qt_app: QApplication, cursor: OverlayVirtualCursor) -> None:
    cursor.move_to(100, 100)  # primeira aparição: surge no alvo
    wait_until(qt_app, lambda: not cursor.layer.moving)
    duration = cursor.layer.move_to_logical(QPointF(700, 400))

    assert MIN_MOVE_MS <= duration <= MAX_MOVE_MS
    assert cursor.layer.moving
    qt_app.processEvents()
    wait_until(qt_app, lambda: not cursor.layer.moving)
    assert cursor.layer.position == QPointF(700, 400)
    assert cursor.layer.active


def test_duration_follows_configured_speed(cursor: OverlayVirtualCursor) -> None:
    cursor.layer.move_to_logical(QPointF(0, 0))
    cursor.layer.settings = CursorSettings(speed=1000)
    assert cursor.layer.move_to_logical(QPointF(500, 0)) == 500
    cursor.layer.settings = CursorSettings(speed=6000)
    assert cursor.layer.move_to_logical(QPointF(0, 0)) == 120  # limite mínimo


def test_click_ripple_animates_then_stops(
    qt_app: QApplication, cursor: OverlayVirtualCursor
) -> None:
    cursor.move_to(300, 300)
    cursor.click()
    assert cursor.layer.animating
    cursor.layer._overlay.grab()  # pinta cursor + ripple sem erro
    wait_until(qt_app, lambda: not cursor.layer.animating)


def test_hides_after_idle_and_overlay_disappears(
    qt_app: QApplication, cursor: OverlayVirtualCursor
) -> None:
    cursor.layer._idle.setInterval(50)
    cursor.move_to(200, 200)
    assert cursor.layer._overlay.isVisible()
    wait_until(qt_app, lambda: not cursor.layer.active)
    assert not cursor.layer._overlay.isVisible()


def test_point_at_highlights_target(qt_app: QApplication, cursor: OverlayVirtualCursor) -> None:
    cursor.point_at(ScreenRect(x=400, y=300, width=200, height=100), label="Salvar")
    assert cursor._annotator.highlights.count == 1
    wait_until(qt_app, lambda: not cursor.layer.moving)
    assert cursor.layer.position == QPointF(500, 350)


def test_disabled_cursor_shows_nothing(cursor: OverlayVirtualCursor) -> None:
    cursor.apply_settings(CursorSettings(enabled=False))
    cursor.move_to(100, 100)
    cursor.click()
    cursor.point_at(ScreenRect(x=0, y=0, width=10, height=10))
    assert not cursor.layer.active
    assert cursor._annotator.highlights.count == 0


def test_module_applies_settings_changes(qt_app: QApplication, tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path, with_ui=True)

    async def scenario() -> tuple[int, str]:
        assert app.settings is not None
        await app.manager.start_all()
        await app.settings.update("cursor", size=40)
        await app.bus.drain(timeout=1)
        module = app.virtual_cursor
        assert module is not None
        cursor = module.cursor  # type: ignore[attr-defined]
        size = cursor.layer.settings.size
        state = str(app.manager.states["virtual_cursor"])
        await app.manager.stop_all()
        return size, state

    size, state = run_on_qt(scenario())
    assert state == "running"
    assert size == 40


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
def test_physical_cursor_never_moves() -> None:
    script = textwrap.dedent(
        """
        import ctypes, time
        from ctypes import wintypes
        from alfred.config.settings import CursorSettings
        from alfred.domain.screen import ScreenRect
        from alfred.ui.overlay import OverlayAnnotator, OverlayWindow
        from alfred.ui.qt_runtime import ensure_qt_application
        from alfred.ui.virtual_cursor import OverlayVirtualCursor

        def physical():
            point = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            return point.x, point.y

        app = ensure_qt_application()
        overlay = OverlayWindow()
        cursor = OverlayVirtualCursor(overlay, OverlayAnnotator(overlay), CursorSettings())
        targets = [(200, 200), (900, 500), (300, 700), (560, 420)]
        samples = []

        def pump(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                samples.append(physical())

        before = physical()
        for x, y in targets[:3]:
            cursor.move_to(x, y)
            pump(1)
            cursor.click()
        cursor.point_at(ScreenRect(x=500, y=400, width=120, height=40), label="OK")
        pump(1)
        # Se o cursor físico estivesse sendo movido, ele pararia exatamente nos alvos.
        landed = [p for p in samples if any(abs(p[0]-x) <= 1 and abs(p[1]-y) <= 1
                                            for x, y in targets)]
        assert not landed, f"cursor físico chegou aos alvos: {landed[:3]}"
        user_moved = len(set(samples)) > 1
        if not user_moved:
            assert physical() == before, (before, physical())
        print("OK", before, "uso humano detectado" if user_moved else "parado")
        """
    )
    env = {**os.environ, "QT_QPA_PLATFORM": "windows"}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
