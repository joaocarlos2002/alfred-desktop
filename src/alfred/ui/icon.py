"""Ícone do ALFRED desenhado em código (identidade própria, sem assets de terceiros)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap, QRadialGradient

from alfred.ui.theme import THEME

OBSIDIAN = THEME.color("obsidian")
GRAPHITE = THEME.color("slate")
ACCENT = THEME.color("accent")


def render_icon_pixmap(size: int = 64) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = size * 0.06
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    gradient = QRadialGradient(QPointF(size * 0.4, size * 0.35), size * 0.6)
    gradient.setColorAt(0.0, GRAPHITE)
    gradient.setColorAt(1.0, OBSIDIAN)
    painter.setBrush(gradient)
    painter.setPen(QPen(ACCENT, max(1.0, size * 0.045)))
    painter.drawEllipse(rect)

    # monograma "A" geométrico
    pen = QPen(ACCENT, max(1.5, size * 0.07))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    top = QPointF(size * 0.5, size * 0.27)
    painter.drawLine(top, QPointF(size * 0.31, size * 0.72))
    painter.drawLine(top, QPointF(size * 0.69, size * 0.72))
    painter.drawLine(QPointF(size * 0.39, size * 0.55), QPointF(size * 0.61, size * 0.55))

    painter.end()
    return pixmap


def alfred_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        icon.addPixmap(render_icon_pixmap(size))
    return icon
