"""Módulo de áudio: liga/desliga a captura conforme ``voice.enabled`` e troca de microfone."""

from __future__ import annotations

import asyncio
import logging

from alfred.config.events import SettingsChanged
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.events import Event
from alfred.domain.module import Health, HealthStatus, Module
from alfred.voice.capture import AudioCapture

logger = logging.getLogger(__name__)


class AudioInputChanged(Event):
    device: str | None
    available: bool


class AudioModule(Module):
    name = "audio"
    depends_on = ("event_bus", "settings")
    optional = True

    def __init__(self, capture: AudioCapture, settings: SettingsService, bus: EventBus) -> None:
        self.capture = capture
        self._settings = settings
        self._bus = bus
        self._subscription: Subscription[SettingsChanged] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        voice = self._settings.current.voice
        if voice.enabled:
            await asyncio.to_thread(self.capture.start, voice.input_device)
        self._subscription = self._bus.subscribe(SettingsChanged, self._on_settings, name="audio")

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        if self.capture.running:
            await asyncio.to_thread(self.capture.stop)

    async def health(self) -> Health:
        if not self._settings.current.voice.enabled:
            return Health(status=HealthStatus.HEALTHY, detail="voz desativada")
        if not self.capture.running:
            return Health(status=HealthStatus.DEGRADED, detail="microfone indisponível")
        return Health(status=HealthStatus.HEALTHY, detail=self.capture.device_name)

    def notify_device_change(self, device: str | None, available: bool) -> None:
        """Chamado pela captura (thread de áudio)."""
        if self._loop is not None:
            self._bus.publish_threadsafe(AudioInputChanged(device=device, available=available))

    async def _on_settings(self, event: SettingsChanged) -> None:
        if event.section != "voice":
            return
        voice = event.settings.voice
        if not voice.enabled:
            if self.capture.running:
                await asyncio.to_thread(self.capture.stop)
                logger.info("Captura de áudio desligada")
            return
        if not self.capture.running:
            await asyncio.to_thread(self.capture.start, voice.input_device)
        elif "input_device" in event.changed:
            await asyncio.to_thread(self.capture.switch_device, voice.input_device)
