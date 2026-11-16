"""Pipeline de voz: ativação → escuta → transcrição (RF-004).

Ativação pela palavra "Alfred" ou pelo atalho (push-to-talk: o segundo toque encerra a escuta).
A escuta termina pelo VAD (fim de fala, nenhuma fala, duração máxima). A transcrição roda fora do
event loop e é publicada como ``UtteranceTranscribed``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Protocol

import numpy as np

from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.assistant import ListeningStarted, ListeningStopped
from alfred.domain.hotkeys import HotkeyAction, HotkeyPressed
from alfred.domain.module import Module
from alfred.domain.voice import SpeakRequested, UtteranceTranscribed, WakeWordDetected
from alfred.voice.capture import FrameListener
from alfred.voice.vad import FRAME_MS, Endpointer, Frame

logger = logging.getLogger(__name__)

PREROLL_MS = 3000
"""Áudio anterior à ativação incluído na escuta: cobre "Alfred, que horas são?" dito de uma vez
(a palavra só é confirmada depois que o trecho termina)."""


class PipelineState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"


class Capture(Protocol):
    @property
    def running(self) -> bool: ...

    def subscribe(self, listener: FrameListener) -> Callable[[], None]: ...


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> Any: ...


class VoicePipeline(Module):
    name = "voice_pipeline"
    depends_on = ("event_bus", "audio", "stt")
    optional = True

    def __init__(
        self,
        bus: EventBus,
        capture: Capture,
        stt: Transcriber,
        *,
        endpointer_factory: Callable[[], Endpointer] = Endpointer,
    ) -> None:
        self._bus = bus
        self._capture = capture
        self._stt = stt
        self._endpointer_factory = endpointer_factory
        self.state = PipelineState.IDLE
        self._frames: list[Frame] = []
        self._endpointer: Endpointer | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._trigger = "hotkey"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._subscriptions: list[Subscription[Any]] = []
        self._finishing: asyncio.Task[None] | None = None
        self._preroll: deque[Frame] = deque(maxlen=PREROLL_MS // FRAME_MS)
        self._preroll_unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._subscriptions = [
            self._bus.subscribe(WakeWordDetected, self._on_wake, name="pipeline-wake"),
            self._bus.subscribe(HotkeyPressed, self._on_hotkey, name="pipeline-hotkey"),
        ]
        self._preroll_unsubscribe = self._capture.subscribe(self._preroll.append)

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        if self._preroll_unsubscribe is not None:
            self._preroll_unsubscribe()
            self._preroll_unsubscribe = None
        self._detach()
        if self._finishing is not None:
            await asyncio.gather(self._finishing, return_exceptions=True)

    # ------------------------------------------------------------------ gatilhos
    async def _on_wake(self, event: WakeWordDetected) -> None:
        await self.begin("wake_word")

    async def _on_hotkey(self, event: HotkeyPressed) -> None:
        if event.action is not HotkeyAction.ACTIVATE:
            return
        if self.state is PipelineState.LISTENING:
            self._schedule_finish("manual")  # segundo toque: encerra a escuta
        else:
            await self.begin("hotkey")

    async def begin(self, trigger: str) -> bool:
        if self.state is not PipelineState.IDLE:
            return False
        if not self._capture.running:
            logger.info("Escuta ignorada: microfone desligado (ative a voz nas configurações)")
            return False
        with self._lock:
            self._endpointer = self._endpointer_factory()
            self._frames = []
            if trigger == "wake_word":
                # A fala que acompanha a palavra pode já ter começado (ou terminado).
                for frame in list(self._preroll):
                    self._frames.append(frame)
                    self._endpointer.process(frame)
        self._trigger = trigger
        self.state = PipelineState.LISTENING
        await self._bus.publish(ListeningStarted())
        self._unsubscribe = self._capture.subscribe(self._on_frame)
        logger.info("Ouvindo (%s)", trigger)
        return True

    # ------------------------------------------------------------------ captura (thread de áudio)
    def _on_frame(self, frame: Frame) -> None:
        with self._lock:
            if self._endpointer is None:
                return
            self._frames.append(frame)
            outcome = self._endpointer.process(frame)
        if outcome is not None:
            self._schedule_finish(outcome)

    def _schedule_finish(self, reason: str) -> None:
        if self._loop is None:
            return

        def start_finish() -> None:
            if self.state is PipelineState.LISTENING and self._finishing is None:
                self._finishing = asyncio.ensure_future(self._finish(reason))

        self._loop.call_soon_threadsafe(start_finish)

    # ------------------------------------------------------------------ fim da escuta
    def _detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def _finish(self, reason: str) -> None:
        try:
            self._detach()
            with self._lock:
                frames, heard = (
                    self._frames,
                    bool(self._endpointer and self._endpointer.heard_speech),
                )
                self._frames, self._endpointer = [], None
            await self._bus.publish(ListeningStopped())
            logger.info("Escuta encerrada (%s)", reason)
            if not heard or not frames:
                return
            self.state = PipelineState.TRANSCRIBING
            audio = np.concatenate(frames)
            try:
                transcript = await asyncio.to_thread(self._stt.transcribe, audio)
            except Exception:
                logger.exception("Falha ao transcrever")
                await self._bus.publish(SpeakRequested(text="Desculpe, não consegui entender."))
                return
            if transcript.text.strip():
                await self._bus.publish(
                    UtteranceTranscribed(
                        text=transcript.text,
                        audio_seconds=transcript.audio_seconds,
                        latency_seconds=transcript.latency_seconds,
                        trigger=self._trigger,
                    )
                )
        finally:
            self.state = PipelineState.IDLE
            self._finishing = None
