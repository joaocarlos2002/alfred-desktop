"""Mantém a inicialização automática do Windows igual a `general.start_with_windows`."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from alfred.config.events import SettingsChanged
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Module

logger = logging.getLogger(__name__)


class AutostartBackend(Protocol):
    def is_enabled(self) -> bool: ...

    def enable(self, command: str | None = None) -> None: ...

    def disable(self) -> None: ...


class AutostartService(Module):
    name = "autostart"
    depends_on = ("event_bus", "settings")
    optional = True

    def __init__(self, backend: AutostartBackend, settings: SettingsService, bus: EventBus):
        self._backend = backend
        self._settings = settings
        self._bus = bus

    async def start(self) -> None:
        await self._apply(self._settings.current.general.start_with_windows)
        self._subscription = self._bus.subscribe(
            SettingsChanged, self._on_settings_changed, name="autostart"
        )

    async def stop(self) -> None:
        self._subscription.unsubscribe()

    async def _on_settings_changed(self, event: SettingsChanged) -> None:
        if event.section == "general" and "start_with_windows" in event.changed:
            await self._apply(event.settings.general.start_with_windows)

    async def _apply(self, wanted: bool) -> None:
        enabled = await asyncio.to_thread(self._backend.is_enabled)
        if wanted and not enabled:
            await asyncio.to_thread(self._backend.enable)
            logger.info("Inicialização automática com o Windows ativada")
        elif not wanted and enabled:
            await asyncio.to_thread(self._backend.disable)
            logger.info("Inicialização automática com o Windows desativada")
