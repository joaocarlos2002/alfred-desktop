"""Captura de tela sob demanda (RF-008).

Nunca há monitoramento contínuo: cada captura é um pedido explícito, sujeito à política
``privacy.screen_capture`` (negar / perguntar / permitir), indicada visualmente no overlay e
registrada no histórico. A imagem fica só em memória — nada é enviado nem salvo automaticamente.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from alfred.automation import window_control
from alfred.core.action_log import ActionLog
from alfred.core.confirmations import ConfirmationService
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.confirmation import ConfirmationDecision
from alfred.domain.module import Module
from alfred.domain.screen import ScreenCaptured, ScreenRect

logger = logging.getLogger(__name__)

Target = Literal["all", "monitor", "region", "window"]


class CaptureDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class Capture:
    png: bytes
    area: ScreenRect


def _grab(area: ScreenRect) -> bytes:
    import mss
    import mss.tools

    with mss.MSS() as sct:
        shot = sct.grab({"left": area.x, "top": area.y, "width": area.width, "height": area.height})
        png = mss.tools.to_png(shot.rgb, shot.size)
        if png is None:
            raise RuntimeError("Falha ao codificar a captura em PNG")
        return bytes(png)


def monitor_areas() -> list[ScreenRect]:
    """Índice 0 = desktop virtual inteiro; 1..N = cada monitor (pixels físicos)."""
    import mss

    with mss.MSS() as sct:
        return [
            ScreenRect(x=m["left"], y=m["top"], width=m["width"], height=m["height"])
            for m in sct.monitors
        ]


class ScreenCaptureService(Module):
    name = "screen_capture"
    depends_on = ("settings", "confirmations", "event_bus")
    optional = True

    def __init__(
        self,
        settings: SettingsService,
        confirmations: ConfirmationService | None,
        action_log: ActionLog,
        bus: EventBus,
        *,
        grab: Callable[[ScreenRect], bytes] = _grab,
        monitors: Callable[[], list[ScreenRect]] = monitor_areas,
        window_rect: Callable[[int], window_control.Rect] = window_control.get_rect,
    ) -> None:
        self._settings = settings
        self._confirmations = confirmations
        self._log = action_log
        self._bus = bus
        self._grab = grab
        self._monitors = monitors
        self._window_rect = window_rect

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def _area(
        self, target: Target, monitor: int, region: ScreenRect | None, hwnd: int | None
    ) -> ScreenRect:
        if target == "region":
            if region is None:
                raise ValueError("Informe a região a capturar")
            return region
        if target == "window":
            if hwnd is None:
                raise ValueError("Informe a janela a capturar")
            r = await asyncio.to_thread(self._window_rect, hwnd)
            return ScreenRect(x=r.x, y=r.y, width=r.width, height=r.height)
        areas = await asyncio.to_thread(self._monitors)
        index = 0 if target == "all" else monitor
        if not 0 <= index < len(areas):
            raise ValueError(f"Monitor {index} inexistente")
        return areas[index]

    async def _allowed(self, reason: str, area: ScreenRect) -> bool:
        policy = self._settings.current.privacy.screen_capture
        if policy == "allow":
            return True
        if policy == "deny" or self._confirmations is None:
            return False
        decision = await self._confirmations.request(
            action="Capturar a tela",
            target=f"{area.width}x{area.height} em ({area.x}, {area.y})",
            impact=f"Motivo: {reason}. A imagem fica só na memória do ALFRED.",
            category="screen_capture",
        )
        return decision is ConfirmationDecision.APPROVED

    async def capture(
        self,
        reason: str,
        *,
        target: Target = "monitor",
        monitor: int = 1,
        region: ScreenRect | None = None,
        hwnd: int | None = None,
    ) -> Capture:
        params = {"alvo": target, "motivo": reason}
        async with self._log.record("screen", "capturar_tela", params=params) as rec:
            area = await self._area(target, monitor, region, hwnd)
            rec.detail["area"] = area.model_dump()
            if not await self._allowed(reason, area):
                policy = self._settings.current.privacy.screen_capture
                rec.deny(f"captura não permitida (política: {policy})")
                raise CaptureDeniedError("Captura de tela não permitida")
            png = await asyncio.to_thread(self._grab, area)
            rec.result = {"bytes": len(png)}
        await self._bus.publish(ScreenCaptured(area=area, reason=reason))
        logger.info("Tela capturada (%s): %dx%d", reason, area.width, area.height)
        return Capture(png=png, area=area)
