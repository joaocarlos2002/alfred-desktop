from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from alfred.domain.screen import ScreenRect
from alfred.ui.overlay import (
    MonitorGeometry,
    OverlayAnnotator,
    OverlayWindow,
    physical_to_logical,
    rect_to_logical,
    virtual_desktop,
)
from alfred.ui.qt_runtime import ensure_qt_application

# Dois monitores: 1920x1080 a 100% e, à direita, um 4K a 150% (3840x2160 físicos).
MIXED = [
    MonitorGeometry(0, 0, 1920, 1080, 1.0),
    MonitorGeometry(1920, 0, 2560, 1440, 1.5),
]


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def test_physical_to_logical_on_mixed_dpi() -> None:
    assert physical_to_logical(100, 100, MIXED) == (100, 100)
    # 300 px físicos dentro do monitor a 150% = 200 px lógicos a partir da origem dele
    assert physical_to_logical(1920 + 300, 150, MIXED) == (1920 + 200, 100)
    # canto inferior direito do 4K
    assert physical_to_logical(1920 + 3839, 2159, MIXED) == pytest.approx(
        (1920 + 2559.33, 1439.33), abs=0.01
    )


def test_rect_conversion_keeps_size_proportional_to_scale() -> None:
    rect = rect_to_logical(ScreenRect(x=1920 + 600, y=300, width=300, height=150), MIXED)
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (2320, 200, 200, 100)


def test_screen_rect_center() -> None:
    assert ScreenRect(x=10, y=20, width=100, height=40).center == (60, 40)


def test_overlay_never_takes_input_or_focus(qt_app: QApplication) -> None:
    overlay = OverlayWindow()
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.Tool
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.geometry() == virtual_desktop()


def test_overlay_is_hidden_without_active_layers_and_expires(qt_app: QApplication) -> None:
    overlay = OverlayWindow()
    annotator = OverlayAnnotator(overlay)
    assert not overlay.isVisible()

    first = annotator.highlight(ScreenRect(x=100, y=100, width=200, height=80), label="Salvar")
    annotator.highlight(ScreenRect(x=400, y=100, width=50, height=50), duration=0.1)
    assert overlay.isVisible()
    overlay.grab()  # pinta destaques e rótulo sem erro

    deadline = time.monotonic() + 2
    while annotator.highlights.count > 1 and time.monotonic() < deadline:
        qt_app.processEvents()
    assert annotator.highlights.count == 1

    annotator.clear(first)
    assert not overlay.isVisible()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
def test_windows_skips_overlay_when_hit_testing() -> None:
    """Na plataforma real: o Windows não entrega cliques ao overlay (click-through)."""
    script = textwrap.dedent(
        """
        import ctypes
        from ctypes import wintypes
        from alfred.domain.screen import ScreenRect
        from alfred.ui.overlay import OverlayAnnotator, OverlayWindow, window_ex_style
        from alfred.ui.overlay import WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_NOACTIVATE
        from alfred.ui.qt_runtime import ensure_qt_application

        app = ensure_qt_application()
        overlay = OverlayWindow()
        annotator = OverlayAnnotator(overlay)
        annotator.highlight(ScreenRect(x=200, y=200, width=400, height=300), duration=30)
        for _ in range(20):
            app.processEvents()
        style = window_ex_style(overlay)
        assert style & WS_EX_TRANSPARENT and style & WS_EX_LAYERED and style & WS_EX_NOACTIVATE
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        hit = user32.WindowFromPoint(wintypes.POINT(400, 350))
        assert hit != int(overlay.winId()), "overlay recebeu o hit-test"
        print("OK")
        """
    )
    env = {**os.environ, "QT_QPA_PLATFORM": "windows"}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
