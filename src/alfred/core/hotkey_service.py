"""Registra os atalhos globais configurados e publica ``HotkeyPressed`` (RF-003)."""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from alfred.config.events import SettingsChanged
from alfred.config.settings import ActivationSettings
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.hotkeys import (
    HotkeyAction,
    HotkeyBackend,
    HotkeyConflictError,
    HotkeyPressed,
    HotkeyRegistrationFailed,
)
from alfred.domain.module import Health, HealthStatus, Module

logger = logging.getLogger(__name__)

_SETTING_FOR_ACTION = {
    HotkeyAction.ACTIVATE: "activate_hotkey",
    HotkeyAction.EMERGENCY: "emergency_hotkey",
}


def _configured(activation: ActivationSettings, action: HotkeyAction) -> str:
    value: str = getattr(activation, _SETTING_FOR_ACTION[action])
    return value


class HotkeyService(Module):
    name = "hotkeys"
    depends_on = ("event_bus", "settings")
    optional = True

    def __init__(self, backend: HotkeyBackend, settings: SettingsService, bus: EventBus) -> None:
        self._backend = backend
        self._settings = settings
        self._bus = bus
        self._registered: dict[HotkeyAction, tuple[int, str]] = {}
        self._lock = asyncio.Lock()

    @property
    def registered(self) -> dict[HotkeyAction, str]:
        return {action: combo for action, (_, combo) in self._registered.items()}

    async def start(self) -> None:
        await asyncio.to_thread(self._backend.start)
        activation = self._settings.current.activation
        async with self._lock:
            for action in HotkeyAction:
                await self._register(action, _configured(activation, action))
        self._subscription = self._bus.subscribe(
            SettingsChanged, self._on_settings_changed, name="hotkeys"
        )

    async def stop(self) -> None:
        self._subscription.unsubscribe()
        async with self._lock:
            for hotkey_id, _ in self._registered.values():
                await asyncio.to_thread(self._backend.unregister, hotkey_id)
            self._registered.clear()
        await asyncio.to_thread(self._backend.stop)

    async def health(self) -> Health:
        missing = [a.value for a in HotkeyAction if a not in self._registered]
        if missing:
            return Health(
                status=HealthStatus.DEGRADED, detail=f"atalhos inativos: {', '.join(missing)}"
            )
        return Health(status=HealthStatus.HEALTHY)

    # ------------------------------------------------------------------ internos
    async def _on_settings_changed(self, event: SettingsChanged) -> None:
        if event.section != "activation":
            return
        async with self._lock:
            for action in HotkeyAction:
                wanted = _configured(event.settings.activation, action)
                current = self._registered.get(action)
                if current is not None and current[1] == wanted:
                    continue
                # Registra o novo antes de soltar o antigo: em caso de conflito o anterior segue
                # funcionando.
                previous = self._registered.pop(action, None)
                if await self._register(action, wanted):
                    if previous is not None:
                        await asyncio.to_thread(self._backend.unregister, previous[0])
                elif previous is not None:
                    self._registered[action] = previous
                    logger.warning("Mantendo o atalho anterior '%s' para %s", previous[1], action)

    async def _register(self, action: HotkeyAction, hotkey: str) -> bool:
        callback = partial(self._pressed, action, hotkey)
        try:
            hotkey_id = await asyncio.to_thread(self._backend.register, hotkey, callback)
        except (HotkeyConflictError, ValueError, OSError) as exc:
            logger.warning("Não foi possível registrar o atalho '%s' (%s): %s", hotkey, action, exc)
            await self._bus.publish(
                HotkeyRegistrationFailed(action=action, hotkey=hotkey, reason=str(exc))
            )
            return False
        self._registered[action] = (hotkey_id, hotkey)
        logger.info("Atalho '%s' registrado para %s", hotkey, action)
        return True

    def _pressed(self, action: HotkeyAction, hotkey: str) -> None:
        """Executado na thread do backend."""
        self._bus.publish_threadsafe(HotkeyPressed(action=action, hotkey=hotkey))
