"""Liga o detector de palavra de ativação à captura, conforme as configurações."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from alfred.config.events import SettingsChanged
from alfred.config.settings import VoiceSettings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Health, HealthStatus, Module
from alfred.domain.voice import VoiceModelStatus, WakeWordDetected
from alfred.voice.capture import AudioCapture
from alfred.voice.models import VOSK_SMALL_PT, ensure_model
from alfred.voice.wakeword import RecognizerFactory, WakeWordDetector, vosk_factory

logger = logging.getLogger(__name__)

type FactoryLoader = Callable[[], RecognizerFactory]


def default_loader(models_root: Path) -> FactoryLoader:
    def load() -> RecognizerFactory:
        return vosk_factory(str(ensure_model(VOSK_SMALL_PT, models_root)))

    return load


class WakeWordModule(Module):
    name = "wake_word"
    depends_on = ("audio", "settings", "event_bus")
    optional = True

    def __init__(
        self,
        capture: AudioCapture,
        settings: SettingsService,
        bus: EventBus,
        loader: FactoryLoader,
    ) -> None:
        self._capture = capture
        self._settings = settings
        self._bus = bus
        self._loader = loader
        self._factory: RecognizerFactory | None = None
        self.detector: WakeWordDetector | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._subscription: Subscription[SettingsChanged] | None = None
        self._lock = asyncio.Lock()
        self.error: str | None = None

    @staticmethod
    def wanted(voice: VoiceSettings) -> bool:
        return voice.enabled and voice.wake_word_enabled

    async def start(self) -> None:
        self._subscription = self._bus.subscribe(SettingsChanged, self._on_settings, name="wake")
        await self._apply(self._settings.current.voice)

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        self._disable()

    async def health(self) -> Health:
        if self.error:
            return Health(status=HealthStatus.DEGRADED, detail=self.error)
        state = "ativa" if self.detector else "desligada"
        return Health(status=HealthStatus.HEALTHY, detail=f"ativação por voz {state}")

    async def _on_settings(self, event: SettingsChanged) -> None:
        if event.section == "voice":
            await self._apply(event.settings.voice)

    async def _apply(self, voice: VoiceSettings) -> None:
        async with self._lock:
            if not self.wanted(voice):
                self._disable()
                return
            if self.detector is not None:
                self.detector.min_confidence = voice.wake_word_confidence
                return
            if self._factory is None:
                await self._bus.publish(VoiceModelStatus(model="wake_word", status="downloading"))
                try:
                    self._factory = await asyncio.to_thread(self._loader)
                except Exception as exc:
                    self.error = f"modelo indisponível: {exc}"
                    logger.exception("Não foi possível preparar a ativação por voz")
                    await self._bus.publish(
                        VoiceModelStatus(model="wake_word", status="failed", detail=str(exc))
                    )
                    return
                self.error = None
                await self._bus.publish(VoiceModelStatus(model="wake_word", status="ready"))
            self.detector = WakeWordDetector(
                self._factory, self._detected, min_confidence=voice.wake_word_confidence
            )
            self._unsubscribe = self._capture.subscribe(self.detector.process)
            logger.info("Ativação por voz ligada")

    def _disable(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self.detector is not None:
            logger.info("Ativação por voz desligada")
        self.detector = None

    def _detected(self, word: str, confidence: float) -> None:
        """Thread de áudio → event bus."""
        self._bus.publish_threadsafe(WakeWordDetected(word=word, confidence=confidence))
