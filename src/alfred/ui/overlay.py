"""Overlay transparente e click-through sobre todos os monitores (RF-007).

A janela só existe na tela enquanto alguma camada está ativa — sem custo em idle. Coordenadas
chegam em pixels físicos (Win32/UIA) e são convertidas para lógicas por monitor, o que mantém
a precisão com escalas de DPI diferentes.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from alfred.domain.module import Module
from alfred.domain.screen import ScreenAnnotator, ScreenRect
from alfred.ui.theme import THEME


@dataclass(frozen=True)
class MonitorGeometry:
    """Monitor em coordenadas lógicas do Qt + fator de escala.

    No Windows o Qt preserva a origem nativa de cada tela e escala apenas o conteúdo:
    ``lógico = origem + (físico - origem) / dpr``.
    """

    x: int
    y: int
    width: int
    height: int
    dpr: float

    def contains_physical(self, px: float, py: float) -> bool:
        return (
            self.x <= px < self.x + self.width * self.dpr
            and self.y <= py < self.y + self.height * self.dpr
        )

    def to_logical(self, px: float, py: float) -> tuple[float, float]:
        return self.x + (px - self.x) / self.dpr, self.y + (py - self.y) / self.dpr


def current_monitors() -> list[MonitorGeometry]:
    return [
        MonitorGeometry(
            s.geometry().x(),
            s.geometry().y(),
            s.geometry().width(),
            s.geometry().height(),
            s.devicePixelRatio(),
        )
        for s in QGuiApplication.screens()
    ]


def physical_to_logical(
    px: float, py: float, monitors: list[MonitorGeometry]
) -> tuple[float, float]:
    for monitor in monitors:
        if monitor.contains_physical(px, py):
            return monitor.to_logical(px, py)
    # Fora de qualquer monitor: usa o primeiro como referência.
    return monitors[0].to_logical(px, py) if monitors else (px, py)


def rect_to_logical(rect: ScreenRect, monitors: list[MonitorGeometry]) -> QRectF:
    x1, y1 = physical_to_logical(rect.x, rect.y, monitors)
    x2, y2 = physical_to_logical(rect.x + rect.width, rect.y + rect.height, monitors)
    return QRectF(QPointF(x1, y1), QPointF(x2, y2))


def virtual_desktop() -> QRect:
    area = QRect()
    for screen in QGuiApplication.screens():
        area = area.united(screen.geometry())
    return area


class OverlayLayer(Protocol):
    @property
    def active(self) -> bool: ...

    def paint(self, painter: QPainter, origin: QPointF) -> None:
        """``origin``: canto superior esquerdo do overlay (coordenadas lógicas globais)."""
        ...


class OverlayWindow(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.layers: list[OverlayLayer] = []
        self.refresh_geometry()
        app = QGuiApplication.instance()
        if isinstance(app, QGuiApplication):
            app.screenAdded.connect(lambda *_: self.refresh_geometry())
            app.screenRemoved.connect(lambda *_: self.refresh_geometry())

    def refresh_geometry(self) -> None:
        self.setGeometry(virtual_desktop())

    def add_layer(self, layer: OverlayLayer) -> None:
        self.layers.append(layer)

    def sync(self) -> None:
        """Mostra o overlay se alguma camada estiver ativa; esconde caso contrário."""
        if any(layer.active for layer in self.layers):
            if not self.isVisible():
                self.show()
                apply_click_through(self)
            self.update()
        elif self.isVisible():
            self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        origin = QPointF(self.geometry().topLeft())
        for layer in self.layers:
            if layer.active:
                layer.paint(painter, origin)
        painter.end()


if sys.platform == "win32":
    import ctypes

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOOLWINDOW = 0x00000080
    CLICK_THROUGH_STYLE = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]

    def window_ex_style(widget: QWidget) -> int:
        return int(_user32.GetWindowLongPtrW(int(widget.winId()), GWL_EXSTYLE))

    def apply_click_through(widget: QWidget) -> None:
        """Garante, no nível do Win32, que a janela não recebe cliques nem foco."""
        if QGuiApplication.platformName() != "windows":
            return
        hwnd = int(widget.winId())
        style = int(_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | CLICK_THROUGH_STYLE)

else:  # pragma: no cover

    def apply_click_through(widget: QWidget) -> None:
        return None


@dataclass
class _Highlight:
    rect: ScreenRect
    label: str | None
    expires_at: float


class HighlightLayer:
    """Retângulos de destaque com rótulo, que expiram sozinhos."""

    def __init__(self, overlay: OverlayWindow) -> None:
        self._overlay = overlay
        self._items: dict[UUID, _Highlight] = {}
        self._timer = QTimer()
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._expire)

    @property
    def active(self) -> bool:
        return bool(self._items)

    @property
    def count(self) -> int:
        return len(self._items)

    def add(self, rect: ScreenRect, label: str | None, duration: float) -> UUID:
        highlight_id = uuid4()
        self._items[highlight_id] = _Highlight(rect, label, time.monotonic() + duration)
        self._timer.start()
        self._overlay.sync()
        return highlight_id

    def remove(self, highlight_id: UUID) -> None:
        self._items.pop(highlight_id, None)
        self._after_change()

    def clear(self) -> None:
        self._items.clear()
        self._after_change()

    def _expire(self) -> None:
        now = time.monotonic()
        for highlight_id in [k for k, v in self._items.items() if v.expires_at <= now]:
            del self._items[highlight_id]
        self._after_change()

    def _after_change(self) -> None:
        if not self._items:
            self._timer.stop()
        self._overlay.sync()

    def paint(self, painter: QPainter, origin: QPointF) -> None:
        monitors = current_monitors()
        r = THEME.radius.sm
        for item in self._items.values():
            area = rect_to_logical(item.rect, monitors).translated(-origin)
            painter.setPen(QPen(THEME.color("accent"), 2))
            painter.setBrush(THEME.color("accent", 40))
            painter.drawRoundedRect(area, r, r)
            if item.label:
                painter.setFont(THEME.font(THEME.typography.size_small, bold=True))
                metrics = painter.fontMetrics()
                pad = THEME.spacing.xs
                width = metrics.horizontalAdvance(item.label) + 2 * pad + 4
                pill = QRectF(
                    area.left(),
                    area.top() - metrics.height() - 2 * pad - 2,
                    width,
                    metrics.height() + 2 * pad,
                )
                if pill.top() < 0:
                    pill.moveTop(area.bottom() + 2)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(THEME.color("accent"))
                painter.drawRoundedRect(pill, r, r)
                painter.setPen(THEME.color("obsidian"))
                painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, item.label)


class OverlayAnnotator(ScreenAnnotator):
    """Implementação do ``ScreenAnnotator`` sobre o overlay Qt."""

    def __init__(self, overlay: OverlayWindow) -> None:
        self.overlay = overlay
        self.highlights = HighlightLayer(overlay)
        overlay.add_layer(self.highlights)

    def highlight(
        self, rect: ScreenRect, *, label: str | None = None, duration: float = 1.5
    ) -> UUID:
        return self.highlights.add(rect, label, duration)

    def clear(self, highlight_id: UUID) -> None:
        self.highlights.remove(highlight_id)

    def clear_all(self) -> None:
        self.highlights.clear()


class OverlayModule(Module):
    name = "overlay"
    optional = True

    def __init__(self) -> None:
        self.overlay: OverlayWindow | None = None
        self.annotator: OverlayAnnotator | None = None

    async def start(self) -> None:
        self.overlay = OverlayWindow()
        self.annotator = OverlayAnnotator(self.overlay)

    async def stop(self) -> None:
        if self.annotator is not None:
            self.annotator.clear_all()
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
        self.overlay = None
        self.annotator = None
