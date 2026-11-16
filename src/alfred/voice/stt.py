"""Reconhecimento de fala em PT-BR com faster-whisper (RF-004).

O modelo é carregado sob demanda e descarregado após um período sem uso, para não manter um
modelo pesado permanentemente em memória (Diretriz 3).
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from alfred.config.events import SettingsChanged
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Health, HealthStatus, Module

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcript:
    text: str
    audio_seconds: float
    latency_seconds: float
    language: str = "pt"

    @property
    def empty(self) -> bool:
        return not self.text.strip()


class WhisperLike(Protocol):
    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]: ...


type ModelFactory = Callable[[str], WhisperLike]


HF_ENVIRONMENT = {
    # Privacidade: o huggingface_hub envia telemetria por padrão.
    "HF_HUB_DISABLE_TELEMETRY": "1",
    # Windows sem modo desenvolvedor não cria symlinks; o cache funciona sem eles.
    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
}


COMMAND_PROMPT = "Alfred, que horas são? Alfred, que dia é hoje? Pare. Abra o navegador."
"""Contexto de domínio para o decodificador: melhora muito comandos curtos ditos logo após
"Alfred" (ex.: sem ele, "Alfred, que horas são?" virava "Alfred que oração")."""


def faster_whisper_factory(models_root: Path) -> ModelFactory:
    def create(size: str) -> WhisperLike:
        for key, value in HF_ENVIRONMENT.items():
            os.environ.setdefault(key, value)
        from faster_whisper import WhisperModel

        kwargs: dict[str, Any] = {
            "device": "cpu",
            "compute_type": "int8",
            "download_root": str(models_root / "whisper"),
        }
        try:
            # Local-first: com o modelo já baixado, nenhuma requisição de rede é feita.
            model: WhisperLike = WhisperModel(size, local_files_only=True, **kwargs)
        except Exception:
            logger.info("Modelo de fala '%s' não encontrado localmente; baixando", size)
            model = WhisperModel(size, **kwargs)
        return model

    return create


class SpeechToText:
    def __init__(
        self,
        factory: ModelFactory,
        *,
        model_size: str = "base",
        idle_unload_seconds: float = 120,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self.model_size = model_size
        self.idle_unload_seconds = idle_unload_seconds
        self._clock = clock
        self._model: WhisperLike | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.loads = 0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def transcribe(self, audio: np.ndarray) -> Transcript:
        """Transcreve áudio int16 a 16 kHz. Bloqueante: chame fora do event loop."""
        started = self._clock()
        with self._lock:
            if self._model is None:
                logger.info("Carregando modelo de fala '%s'", self.model_size)
                self._model = self._factory(self.model_size)
                self.loads += 1
            segments, _info = self._model.transcribe(
                audio.astype(np.float32) / 32768.0,
                language="pt",
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=COMMAND_PROMPT,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            self._schedule_unload()
        transcript = Transcript(
            text=text,
            audio_seconds=round(audio.size / 16000, 2),
            latency_seconds=round(self._clock() - started, 3),
        )
        logger.info(
            "Transcrição: %.1f s de áudio em %.2f s (%s)",
            transcript.audio_seconds,
            transcript.latency_seconds,
            self.model_size,
        )
        return transcript

    def unload(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._model is not None:
                self._model = None
                gc.collect()
                logger.info("Modelo de fala descarregado")

    def configure(self, *, model_size: str, idle_unload_seconds: float) -> None:
        self.idle_unload_seconds = idle_unload_seconds
        if model_size != self.model_size:
            self.model_size = model_size
            self.unload()  # próximo uso carrega o novo tamanho

    def _schedule_unload(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.idle_unload_seconds, self.unload)
        self._timer.daemon = True
        self._timer.start()


class SpeechToTextModule(Module):
    name = "stt"
    depends_on = ("settings", "event_bus")
    optional = True

    def __init__(self, stt: SpeechToText, settings: SettingsService, bus: EventBus) -> None:
        self.stt = stt
        self._settings = settings
        self._bus = bus
        self._subscription: Subscription[SettingsChanged] | None = None

    async def start(self) -> None:
        self._apply()
        self._subscription = self._bus.subscribe(SettingsChanged, self._on_settings, name="stt")

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        self.stt.unload()

    async def health(self) -> Health:
        state = "carregado" if self.stt.loaded else "sob demanda"
        return Health(status=HealthStatus.HEALTHY, detail=f"{self.stt.model_size} ({state})")

    def _apply(self) -> None:
        voice = self._settings.current.voice
        self.stt.configure(
            model_size=voice.stt_model, idle_unload_seconds=voice.stt_idle_unload_seconds
        )

    async def _on_settings(self, event: SettingsChanged) -> None:
        if event.section == "voice":
            self._apply()
            if not event.settings.voice.enabled:
                self.stt.unload()
