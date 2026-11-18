"""Painel rápido do widget (#113): abre quando o mouse passa pelo mordomo do canto.

O painel "floresce" em círculo a partir do orbe e tem:
- faíscas douradas ao abrir;
- o mordomo entrando com mola e acompanhando o mouse;
- luzes de fundo em movimento, granulado e uma borda luminosa que gira;
- o título surgindo letra a letra e os controles subindo em cascata.

Tudo o que aparece é real:
- os interruptores alteram as configurações de verdade (``SettingsService``);
- o cartão de IA mostra o provedor e o modelo configurados;
- os atalhos abrem a seção correspondente das configurações ou o painel de atividades.

Com movimento reduzido (opção "Animações" ou preferência do Windows), o painel aparece e some
sem animação, já no estado final.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from functools import cache
from typing import Any

from PySide6.QtCore import QByteArray, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QCursor,
    QEnterEvent,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QGraphicsEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from alfred.config.settings import Settings
from alfred.ui.assets import butler_hi_pixmap
from alfred.ui.motion import MOTION
from alfred.ui.theme import THEME
from alfred.ui.visual_state import (
    MODE_ENERGY,
    MODE_LABEL,
    MODE_RGB,
    VisualMode,
    approach,
    clamp,
    in_expo,
    out_back,
    out_expo,
    stagger,
    wobble,
)

INK = THEME.stage_color("ink")
MUTED = THEME.stage_color("muted")
GOLD = THEME.stage_color("gold")
GOLD_HI = THEME.stage_color("gold_hi")
BASE = THEME.stage_color("base")
SANS = ["Segoe UI Variable Text", "Segoe UI", "Manrope", "Arial"]
SERIF = ["Georgia", "Cambria", "Times New Roman"]

ICONS = {
    "mic": (
        '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'
    ),
    "wake": '<path d="M3 12h3l3-7 4 14 3-7h5"/>',
    "motion": '<path d="M4 17c4 0 4-10 8-10s4 10 8 10"/><circle cx="4" cy="17" r="1.5"/>',
    "windows": '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8"/>',
    "voice": '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',
    "brain": (
        '<path d="M12 3a6 6 0 0 0-6 6c0 2.5 1.5 3.5 2 5h8c.5-1.5 2-2.5 2-5a6 6 0 0 0-6-6z'
        'M9 18h6M10 21h4"/>'
    ),
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "memory": (
        '<path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3'
        's8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>'
    ),
    "shield": '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
}

PROVIDER_NAMES = {"ollama": "Ollama", "openai_compat": "API externa", "openai": "OpenAI"}
HOTKEY_NAMES = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "space": "Espaço"}

# (ícone, rótulo, destino): seção das configurações ou "activity" (painel de atividades).
TILES = (
    ("voice", "Voz", "voice"),
    ("brain", "Inteligência", "ai"),
    ("bolt", "Automação", "triggers"),
    ("memory", "Memória", "memory"),
    ("shield", "Privacidade", "privacy"),
    ("clock", "Histórico", "activity"),
)

OPEN_S = 0.75
CLOSE_S = 0.45
ANCHOR_INSET = 42  # centro do orbe até o canto do painel


def greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "Bom dia."
    if 12 <= hour < 18:
        return "Boa tarde."
    return "Boa noite."


def format_hotkey(combo: str) -> str:
    """``ctrl+alt+space`` → ``Ctrl + Alt + Espaço``."""
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    return " + ".join(
        HOTKEY_NAMES.get(p.lower(), p.upper() if len(p) == 1 else p.title()) for p in parts
    )


def font(
    families: list[str], pixel_size: float, weight: QFont.Weight = QFont.Weight.Normal
) -> QFont:
    result = QFont()
    result.setFamilies(families)
    result.setPixelSize(max(1, round(pixel_size)))
    result.setWeight(weight)
    return result


def mix(a: QColor, b: QColor, k: float) -> QColor:
    k = clamp(k)
    return QColor(
        round(a.red() + (b.red() - a.red()) * k),
        round(a.green() + (b.green() - a.green()) * k),
        round(a.blue() + (b.blue() - a.blue()) * k),
        round(a.alpha() + (b.alpha() - a.alpha()) * k),
    )


def with_alpha(color: QColor, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(clamp(alpha))
    return result


@cache
def icon_pixmap(name: str, rgb: str, size: int, dpr: float) -> QPixmap:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{rgb}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def draw_icon(painter: QPainter, widget: QWidget, name: str, color: QColor, rect: QRectF) -> None:
    pixmap = icon_pixmap(name, color.name(), round(rect.width()), widget.devicePixelRatioF())
    painter.drawPixmap(rect.topLeft(), pixmap)


@cache
def grain_tile() -> QImage:
    rng = random.Random(7)
    image = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
    for y in range(128):
        for x in range(128):
            v = rng.randint(0, 255)
            image.setPixelColor(x, y, QColor(v, v, v, 16))
    return image


def faded_butler(width: int, dpr: float) -> QPixmap:
    """Mordomo com a parte de baixo dissolvida no fundo do painel."""
    pixmap = butler_hi_pixmap(round(width * dpr))
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    fade = QLinearGradient(0, 0, 0, pixmap.height())
    fade.setColorAt(0.55, THEME.stage_color("black"))
    fade.setColorAt(0.88, THEME.stage_color("black", 0))
    painter.fillRect(pixmap.rect(), fade)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


# ---------------------------------------------------------------------- peças
class RiseEffect(QGraphicsEffect):
    """Entrada em cascata: o item sobe com mola enquanto aparece."""

    LIFT = 18

    def __init__(self) -> None:
        super().__init__()
        self.progress = 1.0

    def set_progress(self, progress: float) -> None:
        if progress != self.progress:
            self.progress = progress
            self.update()

    def boundingRectFor(self, rect: QRectF | QRect) -> QRectF:
        return QRectF(rect).adjusted(0, 0, 0, self.LIFT)

    def draw(self, painter: QPainter) -> None:
        p = self.progress
        if p >= 1:
            self.drawSource(painter)
            return
        if p <= 0:
            return
        result: Any = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        pixmap, offset = result if isinstance(result, tuple) else (result, QPoint())
        painter.save()
        painter.setOpacity(clamp(p * 1.6))
        lift = self.LIFT * (1 - out_back(p))
        painter.drawPixmap(offset + QPoint(0, round(lift)), pixmap)
        painter.restore()


class TitleReveal(QWidget):
    """Título que surge letra a letra (sobe, ganha nitidez e opacidade)."""

    def __init__(self) -> None:
        super().__init__()
        self._font = font(SERIF, 44)
        self._text = ""
        self.elapsed_ms = 1e9

    def set_text(self, text: str) -> None:
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        metrics = QFontMetricsF(self._font)
        return QSize(round(metrics.horizontalAdvance(self._text)) + 8, round(metrics.height()) + 6)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._font)
        metrics = QFontMetricsF(self._font)
        x = 0.0
        for i, char in enumerate(self._text):
            e = out_expo(stagger(self.elapsed_ms, 250 + i * 35, 800))
            painter.setOpacity(e)
            painter.setPen(GOLD_HI if char in ".!?" else INK)
            y = metrics.ascent() + 2 + (1 - e) * metrics.height() * 0.5
            painter.drawText(QPointF(x, y), char)
            x += metrics.horizontalAdvance(char)
        painter.end()


class StatusPill(QWidget):
    """Estado do assistente com uma mini onda que pulsa conforme a atividade."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(28)
        self._font = font(SANS, 10.5, QFont.Weight.Bold)
        self._font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        self.label = MODE_LABEL[VisualMode.IDLE]
        self.color = QColor(GOLD)
        self.energy = MODE_ENERGY[VisualMode.IDLE]
        self.t = 0.0

    def set_label(self, label: str) -> None:
        self.label = label
        self.setAccessibleName(label)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        text = QFontMetricsF(self._font).horizontalAdvance(self.label.upper())
        return QSize(round(10 + 35 + 8 + text + 14), 28)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(THEME.stage_color("white", 22), 1))
        painter.setBrush(THEME.stage_color("white", 16))
        painter.drawRoundedRect(box, box.height() / 2, box.height() / 2)
        pen = QPen(self.color, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        middle = box.center().y()
        for i in range(7):
            height = 3 + abs(wobble(i, self.t * 2)) * 12 * self.energy
            x = 12 + i * 5
            painter.drawLine(QPointF(x, middle - height / 2), QPointF(x, middle + height / 2))
        painter.setPen(INK)
        painter.setFont(self._font)
        painter.drawText(
            QRectF(53, 0, box.width() - 53, box.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.label.upper(),
        )
        painter.end()


class HoverButton(QAbstractButton):
    """Base dos botões pintados à mão: acompanha hover e posição do mouse."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.setText(text)
        self.setAccessibleName(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hover = 0.0
        self.mouse = QPointF(-100, -100)
        self.accent = QColor(GOLD)
        self._hover_target = 0.0
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._hover_step)

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._set_hover(1.0)

    def leaveEvent(self, event: Any) -> None:
        super().leaveEvent(event)
        self._set_hover(0.0)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        self.mouse = event.position()
        self.update()

    def _set_hover(self, target: float) -> None:
        self._hover_target = target
        if MOTION.reduced:
            self.hover = target
            self.update()
        else:
            self._hover_timer.start(16)

    def _hover_step(self) -> None:
        self.hover = approach(self.hover, self._hover_target, 1 / 60, 0.2)
        if abs(self.hover - self._hover_target) < 0.01:
            self.hover = self._hover_target
            self._hover_timer.stop()
        self.update()


class ToggleChip(HoverButton):
    """Interruptor com ícone; o botão desliza com mola."""

    def __init__(self, text: str, icon: str) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.icon_name = icon
        self.knob = 0.0
        self._font = font(SANS, 12, QFont.Weight.DemiBold)
        self._knob_from = 0.0
        self._knob_start = 0.0
        self._knob_timer = QTimer(self)
        self._knob_timer.timeout.connect(self._knob_step)
        self.toggled.connect(self._animate)

    def set_checked_silently(self, checked: bool) -> None:
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._knob_timer.stop()
        self.knob = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(176, 40)

    def _animate(self, checked: bool) -> None:
        if MOTION.reduced:
            self.knob = 1.0 if checked else 0.0
            self.update()
            return
        self._knob_from = self.knob
        self._knob_start = time.monotonic()
        self._knob_timer.start(16)

    def _knob_step(self) -> None:
        p = clamp((time.monotonic() - self._knob_start) / 0.45)
        target = 1.0 if self.isChecked() else 0.0
        self.knob = self._knob_from + (target - self._knob_from) * out_back(p)
        if p >= 1:
            self.knob = target
            self._knob_timer.stop()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        box.translate(0, -2 * self.hover)
        painter.setPen(QPen(THEME.stage_color("white", 18), 1))
        painter.setBrush(
            mix(THEME.stage_color("glass", 150), THEME.stage_color("glass_hover", 190), self.hover)
        )
        painter.drawRoundedRect(box, 14, 14)
        on = self.isChecked()
        icon_color = INK if on else MUTED
        draw_icon(
            painter,
            self,
            self.icon_name,
            icon_color,
            QRectF(box.left() + 10, box.center().y() - 8, 16, 16),
        )
        painter.setFont(self._font)
        painter.setPen(INK)
        text_rect = QRectF(box.left() + 33, box.top(), box.width() - 33 - 46, box.height())
        elided = QFontMetricsF(self._font).elidedText(
            self.text(), Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided
        )
        track = QRectF(box.right() - 44, box.center().y() - 10, 34, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mix(THEME.stage_color("track"), self.accent, self.knob))
        painter.drawRoundedRect(track, 10, 10)
        painter.setBrush(mix(THEME.stage_color("knob"), THEME.stage_color("knob_on"), self.knob))
        painter.drawEllipse(QPointF(track.left() + 10 + 14 * self.knob, track.center().y()), 7, 7)
        painter.end()


class TileButton(HoverButton):
    """Atalho para uma seção, com luz que segue o mouse."""

    def __init__(self, text: str, icon: str, target: str) -> None:
        super().__init__(text)
        self.icon_name = icon
        self.target = target
        self.setFixedHeight(44)
        self._font = font(SANS, 12.5, QFont.Weight.DemiBold)

    def sizeHint(self) -> QSize:
        return QSize(116, 44)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        box.translate(0, -2.5 * self.hover)
        path = QPainterPath()
        path.addRoundedRect(box, 15, 15)
        painter.fillPath(path, THEME.stage_color("glass", 165))
        if self.hover > 0.01:
            light = QRadialGradient(self.mouse, 90)
            light.setColorAt(0, with_alpha(self.accent, 0.32 * self.hover))
            light.setColorAt(1, with_alpha(self.accent, 0))
            painter.fillPath(path, QBrush(light))
        painter.setPen(
            QPen(mix(THEME.stage_color("white", 18), with_alpha(self.accent, 0.55), self.hover), 1)
        )
        painter.drawPath(path)
        draw_icon(
            painter,
            self,
            self.icon_name,
            GOLD_HI,
            QRectF(box.left() + 10, box.center().y() - 8.5, 17, 17),
        )
        painter.setFont(self._font)
        painter.setPen(INK)
        painter.drawText(
            QRectF(box.left() + 34, box.top(), box.width() - 38, box.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )
        painter.end()


class GoldButton(HoverButton):
    """Botão principal dourado com um brilho que atravessa de tempos em tempos."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFixedHeight(46)
        self._font = font(SANS, 13, QFont.Weight.Bold)
        self.t = 0.0

    def sizeHint(self) -> QSize:
        return QSize(220, 46)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(box, 16, 16)
        gradient = QLinearGradient(box.topLeft(), box.bottomRight())
        gradient.setColorAt(0, mix(GOLD_HI, THEME.stage_color("gold_glow"), self.hover * 0.5))
        gradient.setColorAt(1, mix(GOLD, GOLD_HI, self.hover * 0.4))
        painter.fillPath(path, gradient)
        phase = (self.t % 3.2) / 3.2
        if phase < 0.4:
            x = -box.width() + (phase / 0.4) * box.width() * 2
            shine = QLinearGradient(x, 0, x + box.width(), box.height() * 0.3)
            shine.setColorAt(0.3, THEME.stage_color("white", 0))
            shine.setColorAt(0.5, THEME.stage_color("white", 150))
            shine.setColorAt(0.7, THEME.stage_color("white", 0))
            painter.fillPath(path, shine)
        painter.setFont(self._font)
        painter.setPen(THEME.stage_color("gold_text"))
        inner = box.adjusted(16, 0, -16, 0)
        painter.drawText(
            inner, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text()
        )
        painter.drawText(
            inner.translated(3 * self.hover, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            "→",
        )
        painter.end()


class PillButton(HoverButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFixedHeight(28)
        self._font = font(SANS, 12, QFont.Weight.DemiBold)

    def sizeHint(self) -> QSize:
        return QSize(round(QFontMetricsF(self._font).horizontalAdvance(self.text())) + 24, 28)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(
            QPen(mix(THEME.stage_color("white", 36), with_alpha(self.accent, 0.8), self.hover), 1)
        )
        painter.setBrush(with_alpha(self.accent, 0.12 * self.hover))
        painter.drawRoundedRect(box, box.height() / 2, box.height() / 2)
        painter.setFont(self._font)
        painter.setPen(INK)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class AiCard(QWidget):
    """Provedor e modelo de IA configurados, com atalho para trocar."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(58)
        self.t = 0.0
        self.dot = THEME.stage_color("ok")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(34, 8, 12, 8)
        layout.setSpacing(8)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.caption = QLabel("Inteligência")
        self.caption.setFont(font(SANS, 11))
        self.caption.setStyleSheet(f"color:{MUTED.name()};background:transparent")
        self.name = QLabel()
        self.name.setFont(font(SANS, 13, QFont.Weight.DemiBold))
        self.name.setStyleSheet(f"color:{INK.name()};background:transparent")
        texts.addWidget(self.caption)
        texts.addWidget(self.name)
        layout.addLayout(texts, 1)
        self.change = PillButton("Trocar")
        layout.addWidget(self.change, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_provider(self, provider: str, model: str | None, local: bool | None) -> None:
        display = PROVIDER_NAMES.get(provider, provider)
        where = {True: "local", False: "externa", None: ""}[local]
        self.caption.setText(f"Inteligência {where} · {display}".replace("  ", " "))
        self.name.setText(model or "modelo padrão do provedor")
        self.dot = {True: THEME.stage_color("ok"), False: THEME.stage_color("warn"), None: MUTED}[
            local
        ]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(THEME.stage_color("white", 20), 1))
        painter.setBrush(THEME.stage_color("glass", 165))
        painter.drawRoundedRect(box, 16, 16)
        center = QPointF(18, box.center().y())
        pulse = (self.t % 2.0) / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(self.dot, 0.55 * (1 - pulse)))
        painter.drawEllipse(center, 4.5 + 9 * pulse, 4.5 + 9 * pulse)
        painter.setBrush(self.dot)
        painter.drawEllipse(center, 4.5, 4.5)
        painter.end()


# ---------------------------------------------------------------------- painel
class QuickPanel(QWidget):
    action_taken = Signal()  # o usuário abriu outra tela: o painel deve fechar

    WIDTH = 420
    HEIGHT = 600
    RADIUS = 28

    def __init__(
        self,
        *,
        on_change: Callable[[str, dict[str, Any]], None],
        on_open_settings: Callable[[str | None], None],
        on_open_activity: Callable[[], None] | None = None,
        providers: Callable[[], Mapping[str, bool]] | None = None,
        voice_available: bool = False,
        cursor: Callable[[], QPoint] = QCursor.pos,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAccessibleName("ALFRED — ajustes rápidos")
        self._on_change = on_change
        self._on_open_settings = on_open_settings
        self._on_open_activity = on_open_activity
        self._providers = providers
        self._voice_available = voice_available
        self._cursor = cursor
        self._clock = clock

        self._phase = "hidden"  # hidden | opening | open | closing
        self._reveal = 0.0
        self._reveal_from = 0.0
        self._phase_start = 0.0
        self._open_start = -1e9
        self._t0 = clock()
        self._last = self._t0
        self.anchor = QPointF(self.WIDTH - ANCHOR_INSET, self.HEIGHT - ANCHOR_INSET)
        self._max_radius = math.hypot(self.WIDTH, self.HEIGHT)
        self.mode = VisualMode.IDLE
        self._color = [float(c) for c in MODE_RGB[self.mode]]
        self._parallax = QPointF()
        self._sparks: list[list[float]] = []  # x, y, vx, vy, vida, tamanho
        dpr = self._dpr()
        self._butler = faded_butler(300, dpr)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        root = QVBoxLayout(self)
        self._root = root
        root.setContentsMargins(22, 24, 22, 18)
        root.setSpacing(12)
        self.status = StatusPill()
        self.title = TitleReveal()
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setFixedWidth(190)
        self.subtitle.setFont(font(SANS, 13.5))
        self.subtitle.setStyleSheet(f"color:{THEME.stage.sub};background:transparent")
        self.subtitle.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.title, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.subtitle, 0, Qt.AlignmentFlag.AlignLeft)
        root.addStretch(1)

        self.mic = ToggleChip("Microfone", "mic")
        self.wake = ToggleChip("Palavra “Alfred”", "wake")
        self.animations = ToggleChip("Animações", "motion")
        self.autostart = ToggleChip("Iniciar c/ Windows", "windows")
        self.mic.clicked.connect(lambda on: self._on_change("voice", {"enabled": on}))
        self.wake.clicked.connect(self._toggle_wake)
        self.animations.clicked.connect(
            lambda on: self._on_change("appearance", {"animations": on})
        )
        self.autostart.clicked.connect(
            lambda on: self._on_change("general", {"start_with_windows": on})
        )
        self.toggles = (self.mic, self.wake, self.animations, self.autostart)
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, chip in enumerate(self.toggles):
            grid.addWidget(chip, i // 2, i % 2)
        root.addLayout(grid)

        self.ai = AiCard()
        self.ai.change.clicked.connect(lambda: self._open("ai"))
        root.addWidget(self.ai)

        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.tiles: list[TileButton] = []
        for i, (icon, label, target) in enumerate(TILES):
            tile = TileButton(label, icon, target)
            tile.clicked.connect(lambda _=False, t=target: self._open(t))
            tiles.addWidget(tile, i // 3, i % 3)
            self.tiles.append(tile)
        root.addLayout(tiles)

        footer = QHBoxLayout()
        footer.setSpacing(0)
        self.orb_slot_left = QWidget()
        self.orb_slot_left.setFixedWidth(92)
        self.all_settings = GoldButton("Todas as configurações")
        self.all_settings.clicked.connect(lambda: self._open(None))
        self.orb_slot_right = QWidget()
        self.orb_slot_right.setFixedWidth(92)
        footer.addWidget(self.orb_slot_left)
        footer.addWidget(self.all_settings, 1)
        footer.addWidget(self.orb_slot_right)
        self.orb_slot_left.hide()
        root.addLayout(footer)

        # Entrada em cascata (o título tem a própria animação, letra a letra).
        self._rising: list[RiseEffect] = []
        for widget in (
            self.status,
            self.subtitle,
            *self.toggles,
            self.ai,
            *self.tiles,
            self.all_settings,
        ):
            effect = RiseEffect()
            widget.setGraphicsEffect(effect)
            self._rising.append(effect)
        self.title.set_text(greeting(datetime.now().hour))

    # ------------------------------------------------------------------ dados reais
    def load(self, settings: Settings) -> None:
        voice = settings.voice
        self.mic.set_checked_silently(voice.enabled)
        self.wake.set_checked_silently(voice.enabled and voice.wake_word_enabled)
        self.animations.set_checked_silently(settings.appearance.animations)
        self.autostart.set_checked_silently(settings.general.start_with_windows)
        for chip in (self.mic, self.wake):
            chip.setEnabled(self._voice_available)
            chip.setToolTip(
                "" if self._voice_available else "Voz indisponível: nenhum microfone encontrado"
            )
        hotkey = format_hotkey(settings.activation.activate_hotkey)
        if self._voice_available and voice.enabled and voice.wake_word_enabled:
            text = f"Diga <b>“Alfred”</b> ou use <b>{hotkey}</b>. Ajustes rápidos aqui embaixo."
        else:
            text = f"Use <b>{hotkey}</b> para me chamar. Ajustes rápidos aqui embaixo."
        self.subtitle.setText(text)
        self.subtitle.setFixedHeight(self.subtitle.heightForWidth(self.subtitle.width()))
        providers = self._providers() if self._providers is not None else {}
        provider = settings.ai.provider
        self.ai.set_provider(provider, settings.ai.model, providers.get(provider))

    def _toggle_wake(self, on: bool) -> None:
        # Ligar a palavra de ativação também liga o microfone (mesma regra da bandeja).
        if on:
            self._on_change("voice", {"enabled": True, "wake_word_enabled": True})
        else:
            self._on_change("voice", {"wake_word_enabled": False})

    def _open(self, target: str | None) -> None:
        if target == "activity" and self._on_open_activity is not None:
            self._on_open_activity()
        else:
            self._on_open_settings("history" if target == "activity" else target)
        self.action_taken.emit()

    def set_mode(self, mode: VisualMode) -> None:
        self.mode = mode
        self.status.set_label(MODE_LABEL[mode])
        self.status.energy = MODE_ENERGY[mode]
        if not self._motion():
            self._color = [float(c) for c in MODE_RGB[mode]]
            self._apply_accent()

    # ------------------------------------------------------------------ abrir e fechar
    @property
    def is_open(self) -> bool:
        return self._phase in ("opening", "open")

    @property
    def phase(self) -> str:
        return self._phase

    def contains_global(self, point: QPoint) -> bool:
        if self._phase == "hidden" or not self.isVisible():
            return False
        local = QPointF(self.mapFromGlobal(point))
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.RADIUS, self.RADIUS)
        return path.contains(local)

    def place(self, orb_center: QPoint) -> None:
        """Encosta o painel no orbe, crescendo para o lado em que há espaço na tela."""
        screen = QGuiApplication.screenAt(orb_center) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        grow_left = orb_center.x() + ANCHOR_INSET - self.WIDTH >= area.left()
        grow_up = orb_center.y() + ANCHOR_INSET - self.HEIGHT >= area.top()
        x = (
            orb_center.x() + ANCHOR_INSET - self.WIDTH
            if grow_left
            else orb_center.x() - ANCHOR_INSET
        )
        y = (
            orb_center.y() + ANCHOR_INSET - self.HEIGHT
            if grow_up
            else orb_center.y() - ANCHOR_INSET
        )
        x = max(area.left(), min(x, area.right() - self.WIDTH + 1))
        y = max(area.top(), min(y, area.bottom() - self.HEIGHT + 1))
        self.move(x, y)
        self.anchor = QPointF(orb_center - QPoint(x, y))
        corners = (
            QPointF(0, 0),
            QPointF(self.WIDTH, 0),
            QPointF(0, self.HEIGHT),
            QPointF(self.WIDTH, self.HEIGHT),
        )
        self._max_radius = max(
            math.hypot(c.x() - self.anchor.x(), c.y() - self.anchor.y()) for c in corners
        )
        # Deixa espaço para o orbe no canto em que ele fica.
        self.orb_slot_right.setVisible(grow_up and grow_left)
        self.orb_slot_left.setVisible(grow_up and not grow_left)
        self._root.setContentsMargins(22, 24 if grow_up else 84, 22, 18)

    def open_at(self, orb_center: QPoint) -> None:
        fresh = self._phase == "hidden"
        self.place(orb_center)
        now = self._clock()
        self._reveal_from = self._reveal
        self._phase_start = now
        self._phase = "opening"
        if fresh:
            self._open_start = now
            self.title.set_text(greeting(datetime.now().hour))
            self._burst()
        self.show()
        self.raise_()
        if self._motion():
            self._last = now
            self._timer.start(16)
            self._tick()
        else:
            self.finish_animations()

    def close_animated(self) -> None:
        if self._phase == "hidden":
            return
        if not self._motion():
            self._hide_now()
            return
        self._reveal_from = self._reveal
        self._phase_start = self._clock()
        self._phase = "closing"
        if not self._timer.isActive():
            self._last = self._clock()
            self._timer.start(16)

    def finish_animations(self) -> None:
        """Pula para o estado final (movimento reduzido e testes)."""
        self._phase_start -= 60
        self._open_start -= 60
        self._sparks.clear()
        self._tick()
        if not self._motion():
            self._timer.stop()

    def _hide_now(self) -> None:
        self._timer.stop()
        self._phase = "hidden"
        self._reveal = 0.0
        self._sparks.clear()
        self.clearMask()
        self.hide()

    def _motion(self) -> bool:
        return not MOTION.reduced

    def _dpr(self) -> float:
        screen = QGuiApplication.primaryScreen()
        return screen.devicePixelRatio() if screen else 1.0

    def _burst(self) -> None:
        if not self._motion():
            return
        rng = random.Random()
        center = QPointF(self.WIDTH / 2, self.HEIGHT / 2)
        toward = math.atan2(center.y() - self.anchor.y(), center.x() - self.anchor.x())
        for _ in range(90):
            angle = toward + rng.uniform(-0.8, 0.8)
            speed = rng.uniform(3, 8)
            self._sparks.append(
                [
                    self.anchor.x(),
                    self.anchor.y(),
                    math.cos(angle) * speed,
                    math.sin(angle) * speed,
                    1.0,
                    rng.uniform(0.8, 2.2),
                ]
            )

    # ------------------------------------------------------------------ animação
    def _apply_accent(self) -> None:
        accent = QColor(*(round(c) for c in self._color))
        self.status.color = accent
        for chip in self.toggles:
            chip.accent = accent
        for tile in self.tiles:
            tile.accent = accent
        self.ai.change.accent = accent

    def _tick(self) -> None:
        now = self._clock()
        dt = min(0.1, max(0.0, now - self._last))
        self._last = now
        t = now - self._t0
        if self._phase == "opening":
            p = out_expo((now - self._phase_start) / OPEN_S)
            self._reveal = self._reveal_from + (1 - self._reveal_from) * p
            if p >= 1:
                self._phase = "open"
                self._reveal = 1.0
        elif self._phase == "closing":
            self._reveal = self._reveal_from * (1 - in_expo((now - self._phase_start) / CLOSE_S))
            if self._reveal <= 0.001:
                self._hide_now()
                return
        if self._phase in ("opening", "closing"):
            radius = max(1, round(self._max_radius * self._reveal))
            circle = QRect(
                round(self.anchor.x()) - radius,
                round(self.anchor.y()) - radius,
                2 * radius,
                2 * radius,
            )
            self.setMask(
                QRegion(circle, QRegion.RegionType.Ellipse).intersected(QRegion(self.rect()))
            )
        else:
            self.clearMask()

        elapsed = (now - self._open_start) * 1000
        self.title.elapsed_ms = elapsed
        self.title.update()
        for i, effect in enumerate(self._rising):
            effect.set_progress(stagger(elapsed, 350 + i * 60, 700))

        target = MODE_RGB[self.mode]
        self._color = [approach(c, tc, dt) for c, tc in zip(self._color, target, strict=True)]
        self._apply_accent()
        if self.isVisible():
            local = QPointF(self.mapFromGlobal(self._cursor()))
            nx = clamp(local.x() / self.WIDTH, -0.5, 1.5) - 0.5
            ny = clamp(local.y() / self.HEIGHT, -0.5, 1.5) - 0.5
            self._parallax = QPointF(
                approach(self._parallax.x(), -18 * nx, dt, 0.1),
                approach(self._parallax.y(), -12 * ny, dt, 0.1),
            )
        for spark in self._sparks:
            spark[0] += spark[2] * dt * 60
            spark[1] += spark[3] * dt * 60
            spark[2] *= 0.95 ** (dt * 60)
            spark[3] = spark[3] * 0.95 ** (dt * 60) + 0.08 * dt * 60
            spark[4] -= 0.014 * dt * 60
        self._sparks = [s for s in self._sparks if s[4] > 0]

        self.status.t = t
        self.status.update()
        self.ai.t = t
        self.ai.update()
        self.all_settings.t = t
        self.all_settings.update()
        for chip in self.toggles:
            chip.update()
        self.update()

    # ------------------------------------------------------------------ pintura
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = float(self.WIDTH), float(self.HEIGHT)
        moving = self._motion()
        t = self._clock() - self._t0 if moving else 0.0
        accent = QColor(*(round(c) for c in self._color))
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), self.RADIUS, self.RADIUS)
        painter.setClipPath(shape)
        painter.fillRect(self.rect(), BASE)

        # Luzes de fundo (aurora) que derivam devagar.
        blobs = (
            (accent, 0.55, (0.78, 0.75), (-0.22, -0.15), 12.0, 0.62),
            (THEME.stage_color("violet"), 0.35, (0.32, 0.85), (0.28, -0.22), 15.0, 0.55),
            (THEME.stage_color("teal"), 0.28, (0.55, 0.22), (0.1, 0.3), 18.0, 0.5),
        )
        for color, alpha, (cx, cy), (dx, dy), period, size in blobs:
            k = (math.sin(t * math.tau / period) + 1) / 2
            center = QPointF((cx + dx * k) * w, (cy + dy * k) * h)
            radius = size * w * (1 + 0.15 * math.sin(t * math.tau / period + 1))
            glow = QRadialGradient(center, radius)
            glow.setColorAt(0, with_alpha(color, alpha * 0.75))
            glow.setColorAt(1, with_alpha(color, 0))
            painter.fillRect(self.rect(), glow)

        # O mordomo entra com mola e acompanha o mouse.
        elapsed = (self._clock() - self._open_start) * 1000
        spring = out_back(stagger(elapsed, 120, 1000))
        painter.save()
        painter.setOpacity(stagger(elapsed, 120, 500))
        bsize = self._butler.deviceIndependentSize()
        left = w - bsize.width() + 96 + (1 - spring) * 60 + self._parallax.x()
        top = 6 + (1 - spring) * 120 + self._parallax.y()
        center = QPointF(left + bsize.width() / 2, top + bsize.height() / 2)
        painter.translate(center)
        painter.rotate((1 - spring) * 8)
        painter.drawPixmap(QPointF(-bsize.width() / 2, -bsize.height() / 2), self._butler)
        painter.restore()

        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, _vx, _vy, life, radius in self._sparks:
            painter.setBrush(THEME.stage_color("gold_hi", round(255 * clamp(life))))
            painter.drawEllipse(QPointF(x, y), radius, radius)

        step = int(t * 4) % 4
        offsets = ((0, 0), (-4, 3), (3, -4), (-2, -3))
        painter.save()
        painter.translate(*offsets[step])
        painter.drawTiledPixmap(QRectF(-8, -8, w + 16, h + 16), QPixmap.fromImage(grain_tile()))
        painter.restore()

        painter.setClipping(False)
        rim = QConicalGradient(QPointF(w / 2, h / 2), -t * 60)
        rim.setColorAt(0.0, with_alpha(accent, 0))
        rim.setColorAt(0.12, with_alpha(accent, 0.9))
        rim.setColorAt(0.22, THEME.stage_color("white", 40))
        rim.setColorAt(0.4, with_alpha(accent, 0))
        rim.setColorAt(1.0, with_alpha(accent, 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(THEME.stage_color("white", 14), 1))
        painter.drawPath(shape)
        painter.setPen(QPen(QBrush(rim), 1.4))
        painter.drawPath(shape)
        painter.end()
