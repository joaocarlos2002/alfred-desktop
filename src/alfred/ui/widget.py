"""Widget flutuante (RF-002, #113): o mordomo no cantinho da tela.

Um orbe com o rosto do ALFRED e um anel animado que muda com o estado real do assistente
(repouso, ouvindo, pensando, executando, falando, aguardando confirmação, concluído, erro).
Passar o mouse por cima abre o painel rápido (``ui/quick_panel.py``), tirar o mouse fecha e um
clique fixa o painel aberto. O orbe pode ser arrastado para qualquer lugar da tela.

A janela é maior que o orbe para caber o halo e os anéis; só o círculo do orbe reage ao mouse.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from alfred.config.events import SettingsChanged
from alfred.config.settings import Settings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.assistant import STATE_LABELS, AssistantState, AssistantStateChanged
from alfred.domain.module import Module
from alfred.domain.voice import SpeechFinished, SpeechStarted
from alfred.ui.assets import avatar_face
from alfred.ui.motion import MOTION
from alfred.ui.quick_panel import QuickPanel
from alfred.ui.theme import THEME
from alfred.ui.visual_state import (
    MODE_LABEL,
    MODE_RGB,
    VisualMode,
    approach,
    visual_mode,
    wobble,
)

logger = logging.getLogger(__name__)

DRAG_THRESHOLD = 4
EDGE_MARGIN = 18  # distância do orbe até a borda da área de trabalho
FRAME_MS = 16
IDLE_FRAME_MS = 33  # repouso: animação mais calma, menos CPU
EQ_BARS = 56


class OrbFx:
    """Halo e anéis do orbe; separado da janela para ser testável."""

    def __init__(self) -> None:
        self.mode = VisualMode.IDLE
        self.color = [float(c) for c in MODE_RGB[VisualMode.IDLE]]
        self.ripples: list[list[float]] = []  # [raio extra, opacidade]
        self._last_ripple = 0.0
        self.eq = [0.0] * EQ_BARS

    def rgba(self, alpha: float) -> QColor:
        r, g, b = (int(c) for c in self.color)
        return QColor(r, g, b, int(255 * max(0.0, min(1.0, alpha))))

    def step(self, t: float, dt: float) -> None:
        target = MODE_RGB[self.mode]
        self.color = [approach(c, tc, dt) for c, tc in zip(self.color, target, strict=True)]
        rippling = self.mode in (VisualMode.LISTENING, VisualMode.CONFIRM, VisualMode.DONE)
        interval = 0.65 if self.mode is VisualMode.LISTENING else 1.1
        if rippling and t - self._last_ripple > interval:
            self.ripples.append([0.0, 0.8])
            self._last_ripple = t
        for ripple in self.ripples:
            ripple[0] += 48 * dt
            ripple[1] *= 0.965 ** (dt * 60)
        self.ripples = [r for r in self.ripples if r[1] > 0.02]
        for i in range(EQ_BARS):
            amplitude = abs(wobble(i, t * 1.8)) * 17 * (0.6 + 0.4 * math.sin(t * 6 + i * 0.2)) + 2
            self.eq[i] = approach(self.eq[i], amplitude, dt, 0.25)

    def paint(self, painter: QPainter, center: QPointF, radius: float, t: float) -> None:
        painter.save()
        painter.translate(center)
        mode = self.mode
        breath = (math.sin(t * 1.6) + 1) / 2 if mode is VisualMode.IDLE else 1.0
        halo = QRadialGradient(QPointF(0, 0), radius * 2.3)
        halo.setColorAt(0.35, self.rgba(0.35 * breath + 0.15))
        halo.setColorAt(1.0, self.rgba(0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(0, 0), radius * 2.3, radius * 2.3)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        def arc(r: float, start: float, span: float, width: float, alpha: float) -> None:
            pen = QPen(self.rgba(alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            box = QRectF(-r, -r, 2 * r, 2 * r)
            painter.drawArc(box, int(-math.degrees(start) * 16), int(-math.degrees(span) * 16))

        if mode in (VisualMode.IDLE, VisualMode.ERROR):
            speed = 0.25 if mode is VisualMode.ERROR else 1.0
            for k in range(2):
                angle = t * (0.6 + k * 0.35) * (-1 if k else 1) * speed
                span = 1.2 + math.sin(t + k) * 0.4
                arc(radius + 6 + k * 4.5, angle, span, 1.6 - k * 0.5, 0.8 - k * 0.3)
        for extra, alpha in self.ripples:
            painter.setPen(QPen(self.rgba(alpha), 1.4))
            painter.drawEllipse(QPointF(0, 0), radius + extra, radius + extra)
        if mode is VisualMode.LISTENING:
            pen = QPen(self.rgba(0.85), 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            for i in range(72):
                a = i / 72 * math.tau
                v = max(0.0, wobble(i, t * 1.4)) * 9 + 1.5
                c, s = math.cos(a), math.sin(a)
                painter.drawLine(
                    QPointF(c * (radius + 4), s * (radius + 4)),
                    QPointF(c * (radius + 4 + v), s * (radius + 4 + v)),
                )
        if mode in (VisualMode.THINKING, VisualMode.EXECUTING):
            spin = 4.0 if mode is VisualMode.THINKING else 5.5
            arc(radius + 5, t * spin, 1.2 + math.sin(t * 2.2), 2.0, 0.95)
            painter.setPen(Qt.PenStyle.NoPen)
            for k in range(3):
                base = t * (2.2 + k * 0.5) + k * 2.1
                orbit = radius + 12 + k * 4
                for s in range(16):
                    a = base - s * 0.06
                    painter.setBrush(self.rgba((1 - s / 16) * 0.9))
                    dot = 2.5 - s * 0.125
                    painter.drawEllipse(QPointF(math.cos(a) * orbit, math.sin(a) * orbit), dot, dot)
        if mode is VisualMode.SPEAKING:
            for i, length in enumerate(self.eq):
                a = i / EQ_BARS * math.tau - math.pi / 2
                c, s = math.cos(a), math.sin(a)
                inner = QPointF(c * (radius + 5), s * (radius + 5))
                outer = QPointF(c * (radius + 5 + length), s * (radius + 5 + length))
                gradient = QLinearGradient(inner, outer)
                gradient.setColorAt(0, self.rgba(1))
                gradient.setColorAt(1, THEME.stage_color("white", 230))
                pen = QPen(gradient, 2.5)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(inner, outer)
        painter.restore()


class FloatingWidget(QWidget):
    clicked = Signal()
    moved = Signal(int, int)
    hovered = Signal()  # o mouse entrou no orbe
    drag_started = Signal()

    SIZE = 176  # janela (halo e anéis)
    ORB = 64  # diâmetro do orbe

    def __init__(self, *, animations: bool = True) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setMouseTracking(True)
        self.setAccessibleName("ALFRED")
        self._animations = animations
        self._state = AssistantState.IDLE
        self._speaking = False
        self._detail: str | None = None
        self.fx = OrbFx()
        self._t0 = time.monotonic()
        self._last = self._t0
        self._hover = 0.0
        self._inside = False
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        self._face = avatar_face(int(self.ORB * dpr))
        self._face.setDevicePixelRatio(dpr)
        self._press_global: QPoint | None = None
        self._press_window = QPoint()
        self._dragging = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._update_tooltip()

    # ------------------------------------------------------------------ estado
    @property
    def state(self) -> AssistantState:
        return self._state

    @property
    def mode(self) -> VisualMode:
        return self.fx.mode

    @property
    def animating(self) -> bool:
        return self._timer.isActive()

    @property
    def motion_enabled(self) -> bool:
        return self._animations and not MOTION.reduced

    def set_animations(self, enabled: bool) -> None:
        self._animations = enabled
        self._sync_timer()

    def set_state(self, state: AssistantState, detail: str | None = None) -> None:
        self._state = state
        self._detail = detail
        self._apply_mode()

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = speaking
        self._apply_mode()

    def _apply_mode(self) -> None:
        self.fx.mode = visual_mode(self._state, speaking=self._speaking)
        self._update_tooltip()
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        if self.motion_enabled and self.isVisible():
            interval = IDLE_FRAME_MS if self.fx.mode is VisualMode.IDLE else FRAME_MS
            if not self._timer.isActive() or self._timer.interval() != interval:
                self._last = time.monotonic()
                self._timer.start(interval)
            return
        self._timer.stop()
        if not self.motion_enabled:
            self.fx.color = [float(c) for c in MODE_RGB[self.fx.mode]]
        self.update()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event: Any) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        self.fx.step(now - self._t0, dt)
        self._hover = approach(self._hover, 1.0 if self._inside else 0.0, dt, 0.18)
        self.update()

    def _update_tooltip(self) -> None:
        label = MODE_LABEL[self.fx.mode] if self._speaking else STATE_LABELS[self._state]
        text = f"ALFRED — {label}"
        if self._detail:
            text += f"\n{self._detail}"
        self.setToolTip(text + "\nPasse o mouse para os ajustes rápidos")

    # ------------------------------------------------------------------ geometria
    def orb_center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def orb_center_global(self) -> QPoint:
        return self.mapToGlobal(self.rect().center())

    def on_orb(self, local: QPointF) -> bool:
        delta = local - self.orb_center()
        return math.hypot(delta.x(), delta.y()) <= self.ORB / 2 + 4

    def contains_global(self, point: QPoint) -> bool:
        return self.isVisible() and self.on_orb(QPointF(self.mapFromGlobal(point)))

    # ------------------------------------------------------------------ pintura
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = time.monotonic() - self._t0 if self.motion_enabled else 0.0
        center = self.orb_center()
        radius = self.ORB / 2
        self.fx.paint(painter, center, radius, t)

        hover = self._hover if self.motion_enabled else (1.0 if self._inside else 0.0)
        r = radius * (1 + 0.08 * hover)
        painter.save()
        painter.translate(center)
        shadow = QRadialGradient(QPointF(0, 6), r * 1.3)
        shadow.setColorAt(0.6, THEME.stage_color("black", 150))
        shadow.setColorAt(1.0, THEME.stage_color("black", 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(QPointF(0, 6), r * 1.3, r * 1.3)
        body = QRadialGradient(QPointF(0, -r * 0.4), r * 1.4)
        body.setColorAt(0, THEME.stage_color("track"))
        body.setColorAt(1, THEME.stage_color("orb_bottom"))
        painter.setBrush(body)
        painter.drawEllipse(QPointF(0, 0), r, r)
        bob = math.sin(t * 1.4) - 3 * hover
        painter.drawPixmap(
            QRectF(-r, -r + bob, 2 * r, 2 * r), self._face, QRectF(self._face.rect())
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.fx.rgba(0.7), 1.2))
        painter.drawEllipse(QPointF(0, 0), r, r)
        painter.setPen(QPen(THEME.stage_color("orb_edge"), 3))
        painter.setBrush(self.fx.rgba(1))
        painter.drawEllipse(QPointF(r * 0.72, r * 0.72), 6, 6)
        painter.restore()
        painter.end()

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.on_orb(event.position()):
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            self._dragging = False
        else:
            event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_global is None:
            inside = self.on_orb(event.position())
            if inside != self._inside:
                self._inside = inside
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor if inside else Qt.CursorShape.ArrowCursor
                )
                if inside:
                    self.hovered.emit()
                self.update()
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() >= DRAG_THRESHOLD:
            self._dragging = True
            self.drag_started.emit()
        if self._dragging:
            self.move(self._press_window + delta)

    def leaveEvent(self, event: Any) -> None:
        super().leaveEvent(event)
        self._inside = False
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._press_global is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_global = None
        if self._dragging:
            self.moved.emit(self.x(), self.y())
        else:
            self.clicked.emit()
        self._dragging = False


def initial_position(saved: tuple[int, int] | None, size: int, orb: int | None = None) -> QPoint:
    """Posição salva se ainda estiver numa tela visível; senão, cantinho inferior direito.

    ``orb`` é o diâmetro visível dentro da janela de lado ``size`` (o resto é halo): a margem
    até a borda é medida a partir do orbe.
    """
    if saved is not None:
        center = QPoint(saved[0] + size // 2, saved[1] + size // 2)
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().contains(center):
                return QPoint(*saved)
    screen = QGuiApplication.primaryScreen()
    area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
    halo = (size - (orb if orb is not None else size)) // 2
    return QPoint(
        area.right() - size + halo - EDGE_MARGIN, area.bottom() - size + halo - EDGE_MARGIN
    )


class PanelController(QObject):
    """Abre o painel com o mouse por cima, fecha ao sair; um clique fixa o painel aberto."""

    OPEN_DELAY_MS = 180
    CLOSE_DELAY_MS = 420
    POLL_MS = 60

    def __init__(
        self,
        widget: FloatingWidget,
        panel: QuickPanel,
        *,
        cursor: Callable[[], QPoint] = QCursor.pos,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(widget)
        self.widget = widget
        self.panel = panel
        self.pinned = False
        self._cursor = cursor
        self._clock = clock
        self._outside_since: float | None = None
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.timeout.connect(self._open_if_still_hovering)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.check_cursor)
        widget.hovered.connect(self._on_hover)
        widget.clicked.connect(self.toggle_pin)
        widget.drag_started.connect(self.close)
        panel.action_taken.connect(self.close)

    def _on_hover(self) -> None:
        if not self.panel.is_open:
            self._open_timer.start(self.OPEN_DELAY_MS)

    def _open_if_still_hovering(self) -> None:
        if self.widget.contains_global(self._cursor()):
            self.open()

    def open(self) -> None:
        self._open_timer.stop()
        self._outside_since = None
        self.panel.open_at(self.widget.orb_center_global())
        self.widget.raise_()  # o orbe fica por cima do painel
        self._poll.start(self.POLL_MS)

    def close(self) -> None:
        self.pinned = False
        self._open_timer.stop()
        self._poll.stop()
        if self.panel.is_open:
            self.panel.close_animated()

    def toggle_pin(self) -> None:
        if not self.panel.is_open:
            self.open()
            self.pinned = True
        elif self.pinned:
            self.close()
        else:
            self.pinned = True

    def check_cursor(self) -> None:
        if self.pinned or not self.panel.is_open:
            self._outside_since = None
            return
        point = self._cursor()
        if self.panel.contains_global(point) or self.widget.contains_global(point):
            self._outside_since = None
            return
        now = self._clock()
        if self._outside_since is None:
            self._outside_since = now
        elif (now - self._outside_since) * 1000 >= self.CLOSE_DELAY_MS:
            self.close()


class WidgetModule(Module):
    name = "widget"
    depends_on = ("event_bus", "settings", "assistant_state")
    optional = True

    def __init__(
        self,
        *,
        bus: EventBus,
        settings: SettingsService,
        on_open_settings: Callable[[str | None], None] | None = None,
        on_open_activity: Callable[[], None] | None = None,
        ai_providers: Callable[[], Mapping[str, bool]] | None = None,
        voice_available: bool = False,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._on_open_settings = on_open_settings
        self._on_open_activity = on_open_activity
        self._ai_providers = ai_providers
        self._voice_available = voice_available
        self.widget: FloatingWidget | None = None
        self.panel: QuickPanel | None = None
        self.controller: PanelController | None = None
        self._subscriptions: list[Subscription[Any]] = []
        self._pending: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        current = self._settings.current
        appearance = current.appearance
        widget = FloatingWidget(animations=appearance.animations)
        widget.move(initial_position(appearance.widget_position, widget.SIZE, widget.ORB))
        widget.moved.connect(self._persist_position)
        panel = QuickPanel(
            on_change=self._change_setting,
            on_open_settings=self._open_settings,
            on_open_activity=self._on_open_activity,
            providers=self._ai_providers,
            voice_available=self._voice_available,
        )
        panel.load(current)
        self.panel = panel
        self.controller = PanelController(widget, panel)
        widget.setVisible(appearance.widget_visible)
        self.widget = widget
        self._subscriptions = [
            self._bus.subscribe(AssistantStateChanged, self._on_state, name="widget"),
            self._bus.subscribe(SettingsChanged, self._on_settings, name="widget-settings"),
            self._bus.subscribe(SpeechStarted, self._on_speech_started, name="widget-speech"),
            self._bus.subscribe(SpeechFinished, self._on_speech_finished, name="widget-speech-end"),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        if self.controller is not None:
            self.controller.close()
        for window in (self.panel, self.widget):
            if window is not None:
                window.hide()
                window.deleteLater()
        self.widget = None
        self.panel = None
        self.controller = None

    def _sync_mode(self) -> None:
        if self.widget is not None and self.panel is not None:
            self.panel.set_mode(self.widget.mode)

    async def _on_state(self, event: AssistantStateChanged) -> None:
        if self.widget is not None:
            self.widget.set_state(event.state, event.detail)
            self._sync_mode()

    async def _on_speech_started(self, event: SpeechStarted) -> None:
        if self.widget is not None:
            self.widget.set_speaking(True)
            self._sync_mode()

    async def _on_speech_finished(self, event: SpeechFinished) -> None:
        if self.widget is not None:
            self.widget.set_speaking(False)
            self._sync_mode()

    async def _on_settings(self, event: SettingsChanged) -> None:
        if self.panel is not None:
            self.panel.load(event.settings)
        if self.widget is None or event.section != "appearance":
            return
        appearance = event.settings.appearance
        self.widget.setVisible(appearance.widget_visible)
        self.widget.set_animations(appearance.animations)
        if not appearance.widget_visible and self.controller is not None:
            self.controller.close()

    def _open_settings(self, section: str | None) -> None:
        if self._on_open_settings is not None:
            self._on_open_settings(section)

    def _change_setting(self, section: str, changes: dict[str, Any]) -> None:
        task = asyncio.ensure_future(self._apply(section, changes))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _apply(self, section: str, changes: dict[str, Any]) -> Settings | None:
        try:
            return await self._settings.update(section, **changes)
        except Exception:
            logger.exception("Painel rápido: não foi possível alterar %s", section)
            if self.panel is not None:
                self.panel.load(self._settings.current)  # volta ao valor real
            return None

    def _persist_position(self, x: int, y: int) -> None:
        task = asyncio.ensure_future(self._settings.update("appearance", widget_position=(x, y)))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
