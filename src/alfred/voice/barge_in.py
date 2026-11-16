"""Interrupção da fala do ALFRED (barge-in) e proteção contra autoativação (RF-005).

- Atalho de ativação ou palavra de ativação durante a fala → a fala para imediatamente.
- Se o próprio ALFRED vai dizer a palavra de ativação, o detector fica suprimido durante a fala
  (e um pouco depois, pelo eco da sala). Para o resto do eco, a segunda etapa do detector já exige
  a palavra na transcrição — e o eco de um texto sem a palavra não a contém.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.hotkeys import HotkeyAction, HotkeyPressed
from alfred.domain.module import Module
from alfred.domain.voice import SpeechFinished, SpeechStarted, WakeWordDetected
from alfred.voice.wakeword import KEYWORD_RE, normalize

logger = logging.getLogger(__name__)

ECHO_TAIL_SECONDS = 0.6


class Speaker(Protocol):
    @property
    def speaking(self) -> bool: ...

    def stop_speaking(self) -> None: ...


class DetectorHolder(Protocol):
    @property
    def detector(self) -> Any: ...


def mentions_wake_word(text: str) -> bool:
    return bool(KEYWORD_RE.search(normalize(text)))


class BargeInModule(Module):
    name = "barge_in"
    depends_on = ("event_bus", "tts")
    optional = True

    def __init__(
        self,
        bus: EventBus,
        speaker: Speaker,
        wake: DetectorHolder | None = None,
        *,
        echo_tail: float = ECHO_TAIL_SECONDS,
    ) -> None:
        self._bus = bus
        self._speaker = speaker
        self._wake = wake
        self._echo_tail = echo_tail
        self._subscriptions: list[Subscription[Any]] = []
        self._release: asyncio.TimerHandle | None = None
        self.interruptions = 0

    async def start(self) -> None:
        self._subscriptions = [
            self._bus.subscribe(HotkeyPressed, self._on_hotkey, name="barge-hotkey"),
            self._bus.subscribe(WakeWordDetected, self._on_wake, name="barge-wake"),
            self._bus.subscribe(SpeechStarted, self._on_speech_started, name="barge-speech"),
            self._bus.subscribe(SpeechFinished, self._on_speech_finished, name="barge-done"),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        if self._release is not None:
            self._release.cancel()
        self._set_suppressed(False)

    def _interrupt(self, reason: str) -> None:
        if self._speaker.speaking:
            self._speaker.stop_speaking()
            self.interruptions += 1
            logger.info("Fala interrompida (%s)", reason)

    async def _on_hotkey(self, event: HotkeyPressed) -> None:
        if event.action is HotkeyAction.ACTIVATE:
            self._interrupt("atalho")

    async def _on_wake(self, event: WakeWordDetected) -> None:
        self._interrupt("palavra de ativação")

    async def _on_speech_started(self, event: SpeechStarted) -> None:
        if self._release is not None:
            self._release.cancel()
            self._release = None
        if mentions_wake_word(event.text):
            self._set_suppressed(True)

    async def _on_speech_finished(self, event: SpeechFinished) -> None:
        if not mentions_wake_word(event.text):
            return
        loop = asyncio.get_running_loop()
        self._release = loop.call_later(self._echo_tail, self._set_suppressed, False)

    def _set_suppressed(self, value: bool) -> None:
        detector = self._wake.detector if self._wake is not None else None
        if detector is not None:
            detector.suppressed = value
