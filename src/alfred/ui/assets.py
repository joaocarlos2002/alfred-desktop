"""Ilustrações do ALFRED (geradas por ``scripts/build_assets.py``)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen, QPixmap

from alfred.ui.theme import THEME

ASSETS_DIR = Path(__file__).with_name("assets")


@cache
def _load(name: str) -> QPixmap:
    pixmap = QPixmap(str(ASSETS_DIR / name))
    if pixmap.isNull():
        raise FileNotFoundError(ASSETS_DIR / name)
    return pixmap


def butler_pixmap(height: int) -> QPixmap:
    """Ilustração de corpo inteiro (estados vazios, telas de boas-vindas)."""
    return _load("butler.png").scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


def butler_hi_pixmap(width: int) -> QPixmap:
    """Corpo inteiro em resolução maior (painel rápido do widget, #113)."""
    return _load("butler_hi.png").scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)


def avatar_face(size: int) -> QPixmap:
    """Rosto do mordomo recortado em círculo, sem borda (o widget desenha o próprio aro)."""
    source = _load("butler_avatar.png").scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(clip)
    painter.drawPixmap(0, int(size * 0.04), source)
    painter.end()
    return result


def avatar_pixmap(size: int) -> QPixmap:
    """Avatar circular com borda de destaque (cabeçalhos de painéis)."""
    source = _load("butler_avatar.png").scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    circle = QRectF(1, 1, size - 2, size - 2)
    clip = QPainterPath()
    clip.addEllipse(circle)
    painter.fillPath(clip, THEME.color("graphite"))
    painter.setClipPath(clip)
    painter.drawPixmap(0, 0, source)
    painter.setClipping(False)
    painter.setPen(QPen(THEME.color("accent"), 1.5))
    painter.drawEllipse(circle)
    painter.end()
    return result
