"""Resposta por voz (RF-005).

Os sintetizadores (Windows SAPI ou Piper) só produzem PCM; o ``SpeechPlayer`` toca no dispositivo
escolhido. Assim volume, saída e interrupção funcionam igual para as duas engines. O texto é
dividido em frases: a próxima é sintetizada enquanto a atual toca.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from alfred.config.events import SettingsChanged
from alfred.config.settings import VoiceSettings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Health, HealthStatus, Module
from alfred.domain.voice import SpeechFinished, SpeechStarted
from alfred.voice.devices import DeviceBackend, resolve_device
from alfred.voice.models import PIPER_PT_BR_FABER, ensure_files

logger = logging.getLogger(__name__)

PLAYBACK_CHUNK = 1024
SENTENCE_RE = re.compile(r"(?<=[.!?…;:])\s+")

type Audio = np.ndarray


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_RE.split(text.strip()) if part.strip()]


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> tuple[Audio, int]:
        """Devolve (amostras int16 mono, taxa de amostragem)."""
        ...


# ------------------------------------------------------------------ SAPI
class SapiSynthesizer:
    """Vozes do Windows via SAPI, renderizadas em memória (COM inicializado por thread)."""

    SAMPLE_RATE = 22050
    _FORMAT_22KHZ_16BIT_MONO = 22

    def __init__(self, voice: str | None = None, rate: int = 0) -> None:
        self.voice = voice
        self.rate = rate
        self._local = threading.local()

    @staticmethod
    def available_voices() -> list[str]:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        return [token.GetDescription() for token in speaker.GetVoices()]

    def _speaker(self) -> Any:
        speaker = getattr(self._local, "speaker", None)
        if speaker is None:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            tokens = list(speaker.GetVoices())
            wanted = (self.voice or "").casefold()
            chosen = next(
                (t for t in tokens if wanted and wanted in t.GetDescription().casefold()),
                next((t for t in tokens if "portuguese" in t.GetDescription().casefold()), None),
            )
            if chosen is not None:
                speaker.Voice = chosen
            self._local.speaker = speaker
        return speaker

    def synthesize(self, text: str) -> tuple[Audio, int]:
        import win32com.client

        speaker = self._speaker()
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = self._FORMAT_22KHZ_16BIT_MONO
        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        stream.Format = fmt
        speaker.AudioOutputStream = stream
        speaker.Rate = max(-10, min(10, self.rate * 2))
        speaker.Speak(text)
        data = bytes(stream.GetData())
        return np.frombuffer(data, dtype=np.int16).copy(), self.SAMPLE_RATE


# ------------------------------------------------------------------ Piper
class PiperSynthesizer:
    """Voz neural local (Piper). Modelo carregado na primeira síntese."""

    def __init__(self, model_path: Path, rate: int = 0) -> None:
        self.model_path = model_path
        self.rate = rate
        self._voice: Any = None
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> tuple[Audio, int]:
        from piper import SynthesisConfig

        with self._lock:
            if self._voice is None:
                from piper import PiperVoice

                self._voice = PiperVoice.load(str(self.model_path))
            config = SynthesisConfig(length_scale=max(0.5, 1.0 - self.rate * 0.08))
            chunks = list(self._voice.synthesize(text, syn_config=config))
        if not chunks:
            return np.zeros(0, dtype=np.int16), 22050
        audio = np.concatenate([chunk.audio_int16_array for chunk in chunks])
        return audio.astype(np.int16), int(chunks[0].sample_rate)


# ------------------------------------------------------------------ player
class OutputStreamLike(Protocol):
    def start(self) -> None: ...

    def write(self, data: Any) -> Any: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


type OutputFactory = Callable[[int, int | None], OutputStreamLike]
"""(taxa de amostragem, índice do dispositivo) → stream de saída."""


def sounddevice_output(sample_rate: int, device: int | None) -> OutputStreamLike:
    import sounddevice as sd

    stream: OutputStreamLike = sd.RawOutputStream(
        samplerate=sample_rate, channels=1, dtype="int16", device=device, latency="low"
    )
    return stream


@dataclass
class _Utterance:
    text: str


class SpeechPlayer:
    """Fila de falas com interrupção imediata. Callbacks rodam na thread do player."""

    def __init__(
        self,
        synthesizer: Synthesizer,
        *,
        output_factory: OutputFactory = sounddevice_output,
        device: Callable[[], int | None] = lambda: None,
        on_start: Callable[[str], None] | None = None,
        on_finish: Callable[[str, bool], None] | None = None,
    ) -> None:
        self.synthesizer = synthesizer
        self.volume = 0.8
        self._output_factory = output_factory
        self._device = device
        self._on_start = on_start
        self._on_finish = on_finish
        self._queue: queue.SimpleQueue[_Utterance | None] = queue.SimpleQueue()
        self._interrupt = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alfred-tts-synth")
        self._thread = threading.Thread(target=self._run, name="alfred-tts", daemon=True)
        self._speaking = threading.Event()
        self.current_text: str | None = None
        self._thread.start()

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def say(self, text: str, *, interrupt: bool = False) -> None:
        if interrupt:
            self.stop()
        if text.strip():
            self._queue.put(_Utterance(text))

    def stop(self) -> None:
        """Interrompe a fala atual e descarta as pendentes."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._speaking.is_set():
            self._interrupt.set()

    def close(self) -> None:
        self.stop()
        self._queue.put(None)
        self._thread.join(timeout=3)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> None:
        while True:
            utterance = self._queue.get()
            if utterance is None:
                return
            self._interrupt.clear()
            self._speaking.set()
            self.current_text = utterance.text
            if self._on_start:
                self._on_start(utterance.text)
            interrupted = False
            try:
                interrupted = self._play(utterance.text)
            except Exception:
                logger.exception("Falha ao falar")
            finally:
                self._speaking.clear()
                self.current_text = None
                if self._on_finish:
                    self._on_finish(utterance.text, interrupted)

    def _play(self, text: str) -> bool:
        sentences = split_sentences(text) or [text]
        futures: list[Future[tuple[Audio, int]]] = [
            self._executor.submit(self.synthesizer.synthesize, s) for s in sentences
        ]
        stream: OutputStreamLike | None = None
        rate_open: int | None = None
        try:
            for future in futures:
                if self._interrupt.is_set():
                    return True
                audio, rate = future.result()
                if stream is None or rate != rate_open:
                    if stream is not None:
                        stream.close()
                    stream = self._output_factory(rate, self._device())
                    stream.start()
                    rate_open = rate
                scaled = np.clip(audio.astype(np.float32) * self.volume, -32768, 32767)
                samples = scaled.astype(np.int16)
                for start in range(0, samples.size, PLAYBACK_CHUNK):
                    if self._interrupt.is_set():
                        return True
                    stream.write(samples[start : start + PLAYBACK_CHUNK].tobytes())
            return False
        finally:
            for future in futures:
                future.cancel()
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()


# ------------------------------------------------------------------ módulo
type SynthesizerBuilder = Callable[[VoiceSettings], Synthesizer]


def default_builder(models_root: Path) -> SynthesizerBuilder:
    def build(voice: VoiceSettings) -> Synthesizer:
        if voice.tts_engine == "piper":
            folder = ensure_files(PIPER_PT_BR_FABER, models_root / "piper")
            return PiperSynthesizer(folder / f"{PIPER_PT_BR_FABER.name}.onnx", voice.tts_rate)
        return SapiSynthesizer(voice.tts_voice, voice.tts_rate)

    return build


class TtsModule(Module):
    name = "tts"
    depends_on = ("settings", "event_bus")
    optional = True

    def __init__(
        self,
        settings: SettingsService,
        bus: EventBus,
        builder: SynthesizerBuilder,
        *,
        devices: DeviceBackend | None = None,
        output_factory: OutputFactory = sounddevice_output,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._builder = builder
        self._devices = devices
        self._output_factory = output_factory
        self.player: SpeechPlayer | None = None
        self._subscription: Subscription[SettingsChanged] | None = None
        self._engine_key: tuple[str, str | None] | None = None

    async def start(self) -> None:
        import asyncio

        voice = self._settings.current.voice
        synthesizer = await asyncio.to_thread(self._builder, voice)
        self._engine_key = (voice.tts_engine, voice.tts_voice)
        self.player = SpeechPlayer(
            synthesizer,
            output_factory=self._output_factory,
            device=self._output_device,
            on_start=lambda text: self._bus.publish_threadsafe(SpeechStarted(text=text)),
            on_finish=lambda text, interrupted: self._bus.publish_threadsafe(
                SpeechFinished(text=text, interrupted=interrupted)
            ),
        )
        self._apply_tuning(voice)
        self._subscription = self._bus.subscribe(SettingsChanged, self._on_settings, name="tts")

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        if self.player is not None:
            self.player.close()
            self.player = None

    async def health(self) -> Health:
        return Health(
            status=HealthStatus.HEALTHY if self.player else HealthStatus.UNHEALTHY,
            detail=self._engine_key[0] if self._engine_key else None,
        )

    def say(self, text: str, *, interrupt: bool = False) -> None:
        if self.player is not None:
            self.player.say(text, interrupt=interrupt)

    def stop_speaking(self) -> None:
        if self.player is not None:
            self.player.stop()

    @property
    def speaking(self) -> bool:
        return self.player is not None and self.player.speaking

    def _output_device(self) -> int | None:
        if self._devices is None:
            return None
        device = resolve_device(self._devices, "output", self._settings.current.voice.output_device)
        return device.index if device else None

    def _apply_tuning(self, voice: VoiceSettings) -> None:
        if self.player is None:
            return
        self.player.volume = voice.tts_volume / 100
        synthesizer = self.player.synthesizer
        if hasattr(synthesizer, "rate"):
            synthesizer.rate = voice.tts_rate

    async def _on_settings(self, event: SettingsChanged) -> None:
        import asyncio

        if event.section != "voice" or self.player is None:
            return
        voice = event.settings.voice
        key = (voice.tts_engine, voice.tts_voice)
        if key != self._engine_key:
            try:
                self.player.synthesizer = await asyncio.to_thread(self._builder, voice)
                self._engine_key = key
                logger.info("Voz das respostas: %s", voice.tts_engine)
            except Exception:
                logger.exception("Não foi possível trocar a voz para '%s'", voice.tts_engine)
        self._apply_tuning(voice)
