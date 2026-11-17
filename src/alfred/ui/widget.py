"""Widget flutuante: reflete o estado real do assistente (RF-002)."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from alfred.config.events import SettingsChanged
from alfred.config.settings import Settings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.assistant import STATE_LABELS, AssistantState, AssistantStateChanged
from alfred.domain.module import Module
from alfred.ui.icon import render_icon_pixmap
from alfred.ui.motion import MOTION
from alfred.ui.theme import THEME, Theme

logger = logging.getLogger(__name__)

STATE_COLOR = {
    AssistantState.IDLE: "steel",
    AssistantState.LISTENING: "listening",
    AssistantState.PROCESSING: "processing",
    AssistantState.EXECUTING: "executing",
    AssistantState.AWAITING_CONFIRMATION: "confirm",
    AssistantState.DONE: "success",
    AssistantState.ERROR: "error",
}
SPINNING = frozenset({AssistantState.PROCESSING, AssistantState.EXECUTING})
PULSING = frozenset({AssistantState.LISTENING, AssistantState.AWAITING_CONFIRMATION})
DRAG_THRESHOLD = 4
EDGE_MARGIN = 24


class FloatingWidget(QWidget):
    clicked = Signal()
    moved = Signal(int, int)

    SIZE = 56

    def __init__(self, *, theme: Theme = THEME, animations: bool = True) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme = theme
        self._animations = animations
        self._state = AssistantState.IDLE
        self._detail: str | None = None
        self._phase = 0.0
        self._icon = render_icon_pixmap(28)
        self._press_global: QPoint | None = None
        self._press_window = QPoint()
        self._dragging = False

        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(theme.motion.pulse_ms)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._on_phase)
        self._update_tooltip()

    # ------------------------------------------------------------------ estado
    @property
    def state(self) -> AssistantState:
        return self._state

    @property
    def animating(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    def set_animations(self, enabled: bool) -> None:
        self._animations = enabled
        self._sync_animation()

    def set_state(self, state: AssistantState, detail: str | None = None) -> None:
        self._state = state
        self._detail = detail
        self._update_tooltip()
        self._sync_animation()
        self.update()

    def _sync_animation(self) -> None:
        animated = self._animations and not MOTION.reduced and self._state in SPINNING | PULSING
        if animated and not self.animating:
            self._animation.start()
        elif not animated and self.animating:
            self._animation.stop()
            self._phase = 0.0
        self.update()

    def _on_phase(self, value: Any) -> None:
        self._phase = float(value)
        self.update()

    def _update_tooltip(self) -> None:
        text = f"ALFRED — {STATE_LABELS[self._state]}"
        if self._detail:
            text += f"\n{self._detail}"
        self.setToolTip(text)

    # ------------------------------------------------------------------ pintura
    def paintEvent(self, event: QPaintEvent) -> None:
        theme = self._theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(4, 4, -4, -4)

        painter.setPen(QPen(theme.color("slate"), 1))
        painter.setBrush(theme.color("graphite", 235))
        painter.drawEllipse(bounds)

        color = theme.color(STATE_COLOR[self._state])
        ring = bounds.adjusted(2, 2, -2, -2)
        if self._state in SPINNING and self._animations:
            painter.setPen(QPen(theme.color("slate"), 3))
            painter.drawEllipse(ring)
            pen = QPen(color, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(ring, int(-self._phase * 360 * 16), 100 * 16)
        else:
            alpha = 255
            if self._state in PULSING and self._animations:
                alpha = int(140 + 115 * (0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)))
            color.setAlpha(alpha)
            painter.setPen(QPen(color, 3 if self._state is not AssistantState.IDLE else 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(ring)

        icon_rect = QRect(0, 0, self._icon.width(), self._icon.height())
        icon_rect.moveCenter(self.rect().center())
        painter.drawPixmap(icon_rect, self._icon)
        painter.end()

    # ------------------------------------------------------------------ arrastar / clicar
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_global is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() >= DRAG_THRESHOLD:
            self._dragging = True
        if self._dragging:
            self.move(self._press_window + delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._press_global is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_global = None
        if self._dragging:
            self.moved.emit(self.x(), self.y())
        else:
            self.clicked.emit()
        self._dragging = False


def initial_position(saved: tuple[int, int] | None, size: int) -> QPoint:
    """Posição salva se ainda estiver numa tela visível; senão, canto inferior direito."""
    if saved is not None:
        center = QPoint(saved[0] + size // 2, saved[1] + size // 2)
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().contains(center):
                return QPoint(*saved)
    screen = QGuiApplication.primaryScreen()
    area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
    return QPoint(area.right() - size - EDGE_MARGIN, area.bottom() - size - EDGE_MARGIN)


class WidgetModule(Module):
    name = "widget"
    depends_on = ("event_bus", "settings", "assistant_state")
    optional = True

    def __init__(
        self,
        *,
        bus: EventBus,
        settings: SettingsService,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._on_click = on_click
        self.widget: FloatingWidget | None = None
        self._subscriptions: list[Subscription[Any]] = []
        self._pending: set[asyncio.Task[Settings]] = set()

    async def start(self) -> None:
        appearance = self._settings.current.appearance
        widget = FloatingWidget(animations=appearance.animations)
        widget.move(initial_position(appearance.widget_position, widget.SIZE))
        widget.moved.connect(self._persist_position)
        if self._on_click is not None:
            widget.clicked.connect(self._on_click)
        widget.setVisible(appearance.widget_visible)
        self.widget = widget
        self._subscriptions = [
            self._bus.subscribe(AssistantStateChanged, self._on_state, name="widget"),
            self._bus.subscribe(SettingsChanged, self._on_settings, name="widget-settings"),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        if self.widget is not None:
            self.widget.hide()
            self.widget.deleteLater()
            self.widget = None

    async def _on_state(self, event: AssistantStateChanged) -> None:
        if self.widget is not None:
            self.widget.set_state(event.state, event.detail)

    async def _on_settings(self, event: SettingsChanged) -> None:
        if self.widget is None or event.section != "appearance":
            return
        appearance = event.settings.appearance
        self.widget.setVisible(appearance.widget_visible)
        self.widget.set_animations(appearance.animations)

    def _persist_position(self, x: int, y: int) -> None:
        task = asyncio.ensure_future(self._settings.update("appearance", widget_position=(x, y)))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
