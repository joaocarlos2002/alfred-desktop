"""Componentes reutilizáveis do design system v2 (#111).

Todos leem cores, tamanhos e tempos dos tokens (``theme.py``) e respeitam o movimento reduzido
(``motion.py``). Botões, campos, listas, abas, menus, tooltips e barras de progresso vêm do
stylesheet; aqui ficam os componentes com comportamento próprio.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from alfred.ui.motion import MOTION
from alfred.ui.theme import THEME, Shadow


def apply_shadow(widget: QWidget, level: Shadow = THEME.elevation.medium) -> None:
    """Sombra suave de elevação (profundidade sem bordas pesadas)."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(level.blur)
    effect.setOffset(0, level.offset_y)
    effect.setColor(THEME.color("void", level.alpha))
    widget.setGraphicsEffect(effect)


class Card(QFrame):
    """Superfície elevada com borda sutil. ``level=2`` para cartões dentro de painéis."""

    def __init__(self, *, level: int = 1, padding: int = THEME.spacing.lg) -> None:
        super().__init__()
        self.setProperty("role", "card")
        self.setProperty("level", str(level))
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(THEME.spacing.sm)


class Divider(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "divider")
        self.setFixedHeight(1)


class Chip(QLabel):
    """Rótulo em pílula. ``tone``: neutral | accent | success | error."""

    def __init__(self, text: str, *, tone: str = "neutral") -> None:
        super().__init__(text)
        self.setProperty("role", "chip")
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class SectionHeader(QWidget):
    """Sobrelinha dourada + título + subtítulo, a hierarquia padrão das telas."""

    def __init__(self, title: str, subtitle: str = "", *, overline: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, THEME.spacing.sm)
        layout.setSpacing(THEME.spacing.xs)
        if overline:
            label = QLabel(overline.upper())
            label.setProperty("role", "overline")
            layout.addWidget(label)
        self.title = QLabel(title)
        self.title.setProperty("role", "title")
        layout.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setProperty("role", "muted")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        layout.addWidget(self.subtitle)


class EmptyState(QWidget):
    """Estado vazio com orientação (e, opcionalmente, o mascote e uma ação)."""

    action_clicked = Signal()

    def __init__(
        self,
        title: str,
        text: str,
        *,
        image: QPixmap | None = None,
        action: str | None = None,
    ) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(THEME.spacing.sm)
        if image is not None:
            picture = QLabel()
            picture.setPixmap(image)
            picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(picture)
        self.title = QLabel(title)
        self.title.setProperty("role", "subtitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = QLabel(text)
        self.text.setProperty("role", "muted")
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addWidget(self.text)
        self.button: QPushButton | None = None
        if action:
            self.button = QPushButton(action)
            self.button.setProperty("role", "primary")
            self.button.clicked.connect(self.action_clicked)
            row = QHBoxLayout()
            row.addStretch()
            row.addWidget(self.button)
            row.addStretch()
            layout.addLayout(row)


class Toggle(QAbstractButton):
    """Interruptor com o botão deslizando (respeita movimento reduzido)."""

    WIDTH = 40
    HEIGHT = 22

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._offset = 1.0 if checked else 0.0
        super().setChecked(checked)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH, self.HEIGHT)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._anim = MOTION.animate(self, b"offset", 1.0 if checked else 0.0, token="fast")

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1, 1, self.WIDTH - 2, self.HEIGHT - 2)
        on = THEME.color("accent")
        off = THEME.color("charcoal")
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset),
        )
        painter.setPen(QPen(THEME.color("accent" if self.hasFocus() else "steel"), 1))
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob = rect.height() - 6
        x = rect.left() + 3 + (rect.width() - knob - 6) * self._offset
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(THEME.color("ivory" if self.isChecked() else "silver"))
        painter.drawEllipse(QRectF(x, rect.top() + 3, knob, knob))


class StatusDot(QWidget):
    """Indicador de estado com pulso suave (estático com movimento reduzido)."""

    SIZE = 12

    def __init__(self, color: str = "accent") -> None:
        super().__init__()
        self.setFixedSize(self.SIZE * 2, self.SIZE * 2)
        self._color = color
        self._phase = 0.0
        self._pulse = MOTION.variant(self, token="pulse", easing="pulse")
        self._pulse.setStartValue(0.0)
        self._pulse.setEndValue(1.0)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_phase)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def set_active(self, active: bool) -> None:
        if active and not MOTION.reduced:
            self._pulse.setDuration(THEME.motion.pulse_ms)
            self._pulse.start()
        else:
            self._pulse.stop()
            self._phase = 0.0
        self.update()

    @property
    def pulsing(self) -> bool:
        return self._pulse.state() == self._pulse.State.Running

    def _on_phase(self, value: object) -> None:
        self._phase = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        if self._phase:
            halo = self.SIZE / 2 + self.SIZE / 2 * self._phase
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                THEME.color(self._color, int(THEME.elevation.glow_alpha * (1 - self._phase)))
            )
            painter.drawEllipse(center, halo, halo)
        painter.setBrush(THEME.color(self._color))
        painter.drawEllipse(center, self.SIZE / 2 - 1, self.SIZE / 2 - 1)


class Toast(QFrame):
    """Notificação curta que entra deslizando e some sozinha."""

    def __init__(
        self, parent: QWidget, text: str, *, tone: str = "neutral", timeout_ms: int = 3200
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setProperty("level", "2")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            THEME.spacing.lg, THEME.spacing.sm, THEME.spacing.lg, THEME.spacing.sm
        )
        dot = StatusDot({"error": "error", "success": "success"}.get(tone, "accent"))
        layout.addWidget(dot)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)
        apply_shadow(self, THEME.elevation.high)
        self._opacity = QGraphicsOpacityEffect(self)
        self._timeout_ms = timeout_ms

    def show_animated(self) -> None:
        parent = self.parentWidget()
        assert parent is not None
        self.adjustSize()
        width = min(self.sizeHint().width(), parent.width() - THEME.spacing.xl * 2)
        self.resize(width, self.sizeHint().height())
        x = (parent.width() - width) // 2
        rest = parent.height() - self.height() - THEME.spacing.xl
        self.move(x, rest + THEME.spacing.lg)
        self.show()
        self.raise_()
        self._enter = MOTION.animate(
            self, b"pos", self.pos().__class__(x, rest), token="normal", easing="enter"
        )
        QTimer.singleShot(self._timeout_ms, self.dismiss)

    def dismiss(self) -> None:
        if MOTION.reduced:
            self.deleteLater()
            return
        self._exit = MOTION.animate(
            self,
            b"pos",
            self.pos().__class__(self.x(), self.y() + THEME.spacing.lg),
            token="fast",
            easing="exit",
        )
        self._exit.finished.connect(self.deleteLater)


def _clear_effect(widget: QWidget) -> None:
    widget.setGraphicsEffect(None)  # type: ignore[arg-type]  # None remove o efeito


def fade_in(widget: QWidget, token: str = "normal") -> None:
    """Entrada suave de um componente (opacidade 0 → 1)."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = MOTION.animate(effect, b"opacity", 1.0, start=0.0, token=token, easing="enter")
    animation.finished.connect(lambda: _clear_effect(widget))
    if MOTION.reduced:
        _clear_effect(widget)


__all__ = [
    "Card",
    "Chip",
    "Divider",
    "EmptyState",
    "QEasingCurve",
    "SectionHeader",
    "StatusDot",
    "Toast",
    "Toggle",
    "apply_shadow",
    "fade_in",
]
