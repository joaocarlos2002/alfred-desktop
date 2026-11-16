"""Mede o FPS real das animações da interface (meta da Seção 9: 60 FPS).

Uso: ``uv run python scripts/bench_ui.py --seconds 3``

Abre o widget flutuante (estado "processando", animação contínua) e o overlay com o cursor
virtual indo e voltando na tela, conta os ``paintEvent`` efetivamente desenhados e calcula FPS
médio e o percentil 95 do intervalo entre quadros. O cursor virtual não move o mouse físico e o
overlay ignora cliques; tudo some ao final.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from typing import Any

os.environ.pop("QT_QPA_PLATFORM", None)  # mede a renderização real, não a "offscreen"

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPointF, QTimer

from alfred.config.settings import CursorSettings
from alfred.domain.assistant import AssistantState
from alfred.ui.overlay import OverlayWindow
from alfred.ui.qt_runtime import ensure_qt_application
from alfred.ui.virtual_cursor import CursorLayer
from alfred.ui.widget import FloatingWidget


class FrameCounter(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.stamps: list[float] = []

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Paint:
            self.stamps.append(time.perf_counter())
        return False

    def report(self, seconds: float) -> dict[str, Any]:
        gaps = [b - a for a, b in zip(self.stamps, self.stamps[1:], strict=False)]
        if not gaps:
            return {"fps": 0.0, "frames": len(self.stamps)}
        gaps.sort()
        p95 = gaps[min(len(gaps) - 1, int(len(gaps) * 0.95))]
        return {
            "frames": len(self.stamps),
            "fps": round(len(self.stamps) / seconds, 1),
            "frame_ms_median": round(statistics.median(gaps) * 1000, 2),
            "frame_ms_p95": round(p95 * 1000, 2),
        }


def run_for(app: Any, seconds: float) -> None:
    # Loop local: no Qt 6, QApplication.quit() fecha as janelas abertas.
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()
    app = ensure_qt_application()

    widget = FloatingWidget(animations=True)
    widget_frames = FrameCounter()
    widget.installEventFilter(widget_frames)
    widget.show()
    widget.set_state(AssistantState.PROCESSING)
    run_for(app, 0.3)  # aquecimento
    widget_frames.stamps.clear()
    run_for(app, args.seconds)
    widget.set_state(AssistantState.IDLE)
    widget.hide()

    overlay = OverlayWindow()
    cursor = CursorLayer(overlay, CursorSettings(speed=1400))
    overlay.add_layer(cursor)
    overlay_frames = FrameCounter()
    overlay.installEventFilter(overlay_frames)
    area = overlay.geometry()
    left = QPointF(area.left() + area.width() * 0.2, area.center().y())
    right = QPointF(area.left() + area.width() * 0.8, area.center().y())
    targets = [right, left]

    def bounce() -> None:
        if not cursor.moving:
            targets.reverse()
            cursor.move_to_logical(targets[0])

    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(bounce)
    cursor.move_to_logical(left)
    timer.start()
    run_for(app, 0.3)
    overlay_frames.stamps.clear()
    run_for(app, args.seconds)
    timer.stop()
    cursor.hide()

    result = {
        "screen_refresh_hz": round(app.primaryScreen().refreshRate(), 1),
        "widget_processing": widget_frames.report(args.seconds),
        "virtual_cursor_moving": overlay_frames.report(args.seconds),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
