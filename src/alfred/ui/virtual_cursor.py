"""Cursor virtual: camada visual do overlay que mostra onde o ALFRED está agindo (RF-007).

É apenas desenho sobre o overlay click-through: não move o cursor físico, não envia eventos de
mouse e não cria dispositivo virtual. As animações só rodam enquanto o cursor está visível.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QPainter, QPainterPath, QPen, QPolygonF

from alfred.config.events import SettingsChanged
from alfred.config.settings import CursorSettings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Module
from alfred.domain.screen import ScreenRect, VirtualCursor
from alfred.ui.motion import MOTION
from alfred.ui.overlay import OverlayAnnotator, OverlayWindow, current_monitors, physical_to_logical
from alfred.ui.theme import THEME

RIPPLE_MS = 450
IDLE_HIDE_MS = 2500
MIN_MOVE_MS = 120
MAX_MOVE_MS = 900
LABEL = "ALFRED"


@dataclass
class _Ripple:
    center: QPointF
    started: float


class CursorLayer:
    """Estado e pintura do cursor virtual (posição em coordenadas lógicas globais)."""

    def __init__(self, overlay: OverlayWindow, settings: CursorSettings) -> None:
        self._overlay = overlay
        self.settings = settings
        self.position = QPointF(overlay.geometry().center())
        self.visible = False
        self._ripples: list[_Ripple] = []

        self._move = QVariantAnimation(overlay)
        self._move.setEasingCurve(THEME.motion.easing("standard"))
        self._move.valueChanged.connect(self._on_move)

        self._frame = QTimer(overlay)
        self._frame.setInterval(THEME.motion.frame_ms)
        self._frame.timeout.connect(self._on_frame)

        self._idle = QTimer(overlay)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_HIDE_MS)
        self._idle.timeout.connect(self.hide)

    @property
    def active(self) -> bool:
        return self.visible

    @property
    def moving(self) -> bool:
        return self._move.state() == QVariantAnimation.State.Running

    @property
    def animating(self) -> bool:
        return self.moving or self._frame.isActive()

    # ------------------------------------------------------------------ ações
    def move_to_logical(self, target: QPointF) -> int:
        """Anima até ``target``; devolve a duração em ms."""
        if not self.settings.enabled:
            return 0
        start = self.position if self.visible else target
        distance = math.hypot(target.x() - start.x(), target.y() - start.y())
        duration = int(min(MAX_MOVE_MS, max(MIN_MOVE_MS, distance / self.settings.speed * 1000)))
        self._show()
        self._move.stop()
        self._move.setStartValue(start)
        self._move.setEndValue(target)
        if MOTION.reduced:
            duration = 0  # movimento reduzido: o cursor aparece direto no alvo
        self._move.setDuration(duration)
        self._move.start()
        return duration

    def click(self) -> None:
        if not self.settings.enabled:
            return
        self._show()
        self._ripples.append(_Ripple(QPointF(self.position), time.monotonic()))
        self._frame.start()

    def hide(self) -> None:
        self._move.stop()
        self._frame.stop()
        self._ripples.clear()
        self.visible = False
        self._overlay.sync()

    def _show(self) -> None:
        self.visible = True
        self._idle.start()
        self._overlay.sync()

    def _on_move(self, value: Any) -> None:
        self.position = QPointF(value)
        self._idle.start()
        self._overlay.update()

    def _on_frame(self) -> None:
        now = time.monotonic()
        self._ripples = [r for r in self._ripples if (now - r.started) * 1000 < RIPPLE_MS]
        if not self._ripples:
            self._frame.stop()
        self._overlay.update()

    # ------------------------------------------------------------------ pintura
    def paint(self, painter: QPainter, origin: QPointF) -> None:
        tip = self.position - origin
        now = time.monotonic()
        for ripple in self._ripples:
            progress = min(1.0, (now - ripple.started) * 1000 / RIPPLE_MS)
            color = THEME.color("accent", int(200 * (1 - progress)))
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = 6 + 22 * progress
            painter.drawEllipse(ripple.center - origin, radius, radius)

        size = float(self.settings.size)
        arrow = QPolygonF(
            [
                tip,
                tip + QPointF(0, size),
                tip + QPointF(size * 0.28, size * 0.74),
                tip + QPointF(size * 0.5, size * 1.08),
                tip + QPointF(size * 0.64, size * 1.0),
                tip + QPointF(size * 0.42, size * 0.66),
                tip + QPointF(size * 0.76, size * 0.66),
            ]
        )
        path = QPainterPath()
        path.addPolygon(arrow)
        path.closeSubpath()
        painter.setPen(QPen(THEME.color("obsidian"), 1.6))
        painter.setBrush(THEME.color("accent"))
        painter.drawPath(path)

        painter.setFont(THEME.font(THEME.typography.size_small, bold=True))
        metrics = painter.fontMetrics()
        pad = THEME.spacing.xs
        label_pos = tip + QPointF(size * 0.8, size * 0.9)
        width = metrics.horizontalAdvance(LABEL) + 2 * pad
        height = metrics.height() + pad
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(THEME.color("graphite", 230))
        painter.drawRoundedRect(QRectF(label_pos.x(), label_pos.y(), width, height), 4, 4)
        painter.setPen(THEME.color("accent"))
        painter.drawText(
            int(label_pos.x() + pad), int(label_pos.y() + pad / 2 + metrics.ascent()), LABEL
        )


class OverlayVirtualCursor(VirtualCursor):
    """Implementação do contrato ``VirtualCursor`` sobre o overlay."""

    def __init__(
        self, overlay: OverlayWindow, annotator: OverlayAnnotator, settings: CursorSettings
    ) -> None:
        self.layer = CursorLayer(overlay, settings)
        self._annotator = annotator
        overlay.add_layer(self.layer)

    def move_to(self, x: int, y: int) -> None:
        lx, ly = physical_to_logical(x, y, current_monitors())
        self.layer.move_to_logical(QPointF(lx, ly))

    def click(self) -> None:
        self.layer.click()

    def point_at(self, rect: ScreenRect, *, label: str | None = None) -> None:
        if self.layer.settings.enabled:
            self._annotator.highlight(rect, label=label, duration=IDLE_HIDE_MS / 1000)
        self.move_to(*rect.center)

    def hide(self) -> None:
        self.layer.hide()

    def apply_settings(self, settings: CursorSettings) -> None:
        self.layer.settings = settings
        if not settings.enabled:
            self.layer.hide()


class VirtualCursorModule(Module):
    name = "virtual_cursor"
    depends_on = ("overlay", "settings", "event_bus")
    optional = True

    def __init__(self, *, overlay: Any, settings: SettingsService, bus: EventBus) -> None:
        """``overlay``: o ``OverlayModule`` (já iniciado quando este módulo inicia)."""
        self._overlay_module = overlay
        self._settings = settings
        self._bus = bus
        self.cursor: OverlayVirtualCursor | None = None
        self._subscription: Subscription[SettingsChanged] | None = None

    async def start(self) -> None:
        overlay, annotator = self._overlay_module.overlay, self._overlay_module.annotator
        if overlay is None or annotator is None:
            raise RuntimeError("Overlay indisponível")
        self.cursor = OverlayVirtualCursor(overlay, annotator, self._settings.current.cursor)
        self._subscription = self._bus.subscribe(
            SettingsChanged, self._on_settings, name="virtual-cursor"
        )

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        if self.cursor is not None:
            self.cursor.hide()
        self.cursor = None

    async def _on_settings(self, event: SettingsChanged) -> None:
        if self.cursor is not None and event.section == "cursor":
            self.cursor.apply_settings(event.settings.cursor)
