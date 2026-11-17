"""Política de movimento (#111): animações com tokens e respeito ao movimento reduzido.

O movimento é reduzido quando o usuário desliga "Animações" nas configurações **ou** quando o
Windows está com "Efeitos de animação" desligado (Acessibilidade > Efeitos visuais). Com movimento
reduzido, toda animação vira um salto direto para o estado final — a informação continua lá, só
sem o movimento.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QAbstractAnimation, QObject, QPropertyAnimation, QVariantAnimation

from alfred.ui.theme import THEME

SPI_GETCLIENTAREAANIMATION = 0x1042


def system_prefers_reduced_motion() -> bool:
    """``True`` quando o Windows está com as animações de interface desligadas."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    enabled = wintypes.BOOL(True)
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
    )
    return bool(ok) and not enabled.value


class MotionPolicy:
    def __init__(self, system: Callable[[], bool] = system_prefers_reduced_motion) -> None:
        self._system = system
        self._user_enabled = True
        self.reduced = system()

    def configure(self, *, animations_enabled: bool) -> None:
        self._user_enabled = animations_enabled
        self.reduced = (not animations_enabled) or self._system()

    def duration(self, token: str) -> int:
        """Duração (ms) do token de movimento, ou 0 com movimento reduzido."""
        return 0 if self.reduced else int(getattr(THEME.motion, f"{token}_ms"))

    def animate(
        self,
        target: QObject,
        prop: bytes,
        end: Any,
        *,
        start: Any = None,
        token: str = "normal",
        easing: str = "standard",
        parent: QObject | None = None,
    ) -> QPropertyAnimation:
        """Anima ``prop`` até ``end``. Com movimento reduzido, aplica ``end`` na hora."""
        animation = QPropertyAnimation(target, prop, parent or target)
        if start is not None:
            animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(self.duration(token))
        animation.setEasingCurve(THEME.motion.easing(easing))
        if self.reduced:
            target.setProperty(prop.decode(), end)
        else:
            animation.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        return animation

    def variant(
        self, parent: QObject, *, token: str = "normal", easing: str = "standard"
    ) -> QVariantAnimation:
        animation = QVariantAnimation(parent)
        animation.setDuration(self.duration(token))
        animation.setEasingCurve(THEME.motion.easing(easing))
        return animation


MOTION = MotionPolicy()
