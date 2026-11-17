"""Skill de ditado por voz (#124): falar no microfone e o ALFRED escrever no app em foco.

O ditado reaproveita o pipeline de voz local (Whisper): enquanto ativo, a escuta é re-armada sem
precisar da palavra de ativação e cada frase transcrita é digitada pelo ``InputService`` (com a
``InputLock`` e pausando quando o usuário digita). O áudio nunca sai do computador nem é gravado.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from alfred.core.commands import CommandHandler, CommandResult
from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.emergency import EmergencyStopped
from alfred.domain.voice import (
    DictationStarted,
    DictationStopped,
    SpeechFinished,
    SpeechStarted,
    UtteranceTranscribed,
)
from alfred.skills.base import Skill

logger = logging.getLogger(__name__)

TRIGGER = "dictation"
START_GRACE_SECONDS = 1.5
"""Espera a confirmação falada terminar antes de começar a ouvir (para não digitá-la)."""
AFTER_SPEECH_SECONDS = 0.4

STOP_PHRASES = frozenset(
    {"fim do ditado", "encerrar ditado", "parar ditado", "terminar ditado", "sair do ditado"}
)
DELETE_PHRASES = frozenset({"apagar ultima frase", "apaga isso", "apagar isso", "desfazer"})
HALLUCINATIONS = (
    "obrigado por assistir",
    "legendas pela comunidade",
    "amara.org",
    "inscreva-se no canal",
    "legendado por",
)
"""Frases que o Whisper costuma "ouvir" em silêncio ou ruído."""

# (frase falada normalizada, símbolo) — as mais longas primeiro.
SPOKEN: tuple[tuple[tuple[str, ...], str], ...] = tuple(
    sorted(
        (
            (tuple(phrase.split()), symbol)
            for phrase, symbol in {
                "ponto de interrogacao": "?",
                "ponto de exclamacao": "!",
                "ponto e virgula": ";",
                "ponto final": ".",
                "dois pontos": ":",
                "reticencias": "...",
                "virgula": ",",
                "ponto": ".",
                "novo paragrafo": "\n\n",
                "nova linha": "\n",
                "proxima linha": "\n",
            }.items()
        ),
        key=lambda item: -len(item[0]),
    )
)
SENTENCE_END = (".", "?", "!", "\n")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s.-]", " ", text).replace(".", " ").split())


@dataclass(frozen=True)
class DictationAction:
    typed: str = ""
    backspaces: int = 0
    stop: bool = False


@dataclass
class DictationText:
    """Transforma frases transcritas em texto a digitar (pontuação falada, maiúsculas, espaços)."""

    chunks: list[str] = field(default_factory=list)

    @property
    def written(self) -> str:
        return "".join(self.chunks)

    def feed(self, raw: str) -> DictationAction:
        norm = _norm(raw)
        if not norm:
            return DictationAction()
        if norm in STOP_PHRASES:
            return DictationAction(stop=True)
        if norm in DELETE_PHRASES:
            return DictationAction(backspaces=len(self.chunks.pop()) if self.chunks else 0)
        lowered = raw.casefold()
        if any(phrase in lowered for phrase in HALLUCINATIONS):
            return DictationAction()
        chunk = self._render(raw.split())
        if chunk:
            self.chunks.append(chunk)
        return DictationAction(typed=chunk)

    def _render(self, words: list[str]) -> str:
        before = self.written
        out = ""
        capitalize = not before or before.rstrip(" ").endswith(SENTENCE_END)
        i = 0
        while i < len(words):
            symbol, size = self._spoken_at(words, i)
            if symbol is not None:
                out = out.rstrip(" ") + symbol
                capitalize = symbol.endswith(SENTENCE_END)
                i += size
                continue
            word = words[i]
            if capitalize and word[:1].isalpha():
                word = word[0].upper() + word[1:]
            capitalize = word.endswith(SENTENCE_END)
            context = (before + out)[-1:] if (before or out) else ""
            if context and context not in ("\n", " "):
                out += " "
            out += word
            i += 1
        return out

    @staticmethod
    def _spoken_at(words: list[str], i: int) -> tuple[str | None, int]:
        for phrase, symbol in SPOKEN:
            window = words[i : i + len(phrase)]
            if len(window) == len(phrase) and tuple(_norm(w) for w in window) == phrase:
                return symbol, len(phrase)
        return None, 0


class Pipeline(Protocol):
    state: Any

    async def begin(self, trigger: str) -> bool: ...


class Keyboard(Protocol):
    async def type_text(self, text: str) -> None: ...

    async def press(self, hotkey: str) -> None: ...


class DictationSkill(Skill):
    key: ClassVar[str] = "ditado"
    name: ClassVar[str] = "Ditado por voz"
    description: ClassVar[str] = 'Diga "Alfred, ditado" e o ALFRED escreve o que você falar'

    def __init__(
        self,
        bus: EventBus,
        pipeline: Pipeline,
        keyboard: Keyboard,
        *,
        rearm_interval: float = 0.15,
        start_grace: float = 0.0,
    ) -> None:
        self._bus = bus
        self._pipeline = pipeline
        self._keyboard = keyboard
        self._rearm_interval = rearm_interval
        self._start_grace = start_grace
        self.active = False
        self._text = DictationText()
        self._loop: asyncio.Task[None] | None = None
        self._speaking = False
        self._quiet_at = 0.0
        self._subscriptions: list[Subscription[Any]] = []

    # ------------------------------------------------------------------ contrato
    def commands(self) -> list[tuple[str, CommandHandler]]:
        async def start(_: re.Match[str]) -> CommandResult:
            await self.start_session()
            return CommandResult("Ditado ativado. Diga fim do ditado para encerrar.", tool="ditado")

        return [(r"(?:alfred\s+)?(?:modo |iniciar |comecar |começar )?ditado", start)]

    async def activate(self) -> None:
        self._subscriptions = [
            self._bus.subscribe(UtteranceTranscribed, self._on_utterance, name="dictation"),
            self._bus.subscribe(EmergencyStopped, self._on_emergency, name="dictation-stop"),
            self._bus.subscribe(SpeechStarted, self._on_speech_started, name="dictation-tts"),
            self._bus.subscribe(SpeechFinished, self._on_speech_finished, name="dictation-tts-end"),
        ]

    async def deactivate(self) -> None:
        await self.stop_session()
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        self._subscriptions = []

    # ------------------------------------------------------------------ sessão
    async def start_session(self) -> None:
        if self.active:
            return
        self.active = True
        self._text = DictationText()
        loop = asyncio.get_running_loop()
        self._quiet_at = loop.time() + self._start_grace
        self._loop = asyncio.create_task(self._rearm(), name="ditado:escuta")
        await self._bus.publish(DictationStarted())
        logger.info("Ditado iniciado")

    async def stop_session(self) -> None:
        if not self.active:
            return
        self.active = False
        if self._loop is not None:
            self._loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop
            self._loop = None
        await self._bus.publish(DictationStopped(characters=len(self._text.written)))
        logger.info("Ditado encerrado (%d caracteres)", len(self._text.written))

    async def _rearm(self) -> None:
        """Mantém a escuta armada enquanto o ditado estiver ativo e o ALFRED não estiver falando."""
        loop = asyncio.get_running_loop()
        while self.active:
            quiet = not self._speaking and loop.time() >= self._quiet_at
            if quiet and str(self._pipeline.state) == "idle":
                await self._pipeline.begin(TRIGGER)
            await asyncio.sleep(self._rearm_interval)

    # ------------------------------------------------------------------ eventos
    async def _on_utterance(self, event: UtteranceTranscribed) -> None:
        if not self.active or event.trigger != TRIGGER:
            return
        action = self._text.feed(event.text)
        if action.stop:
            await self.stop_session()
            return
        for _ in range(action.backspaces):
            await self._keyboard.press("backspace")
        if action.typed:
            await self._keyboard.type_text(action.typed)

    async def _on_emergency(self, event: EmergencyStopped) -> None:
        await self.stop_session()

    async def _on_speech_started(self, event: SpeechStarted) -> None:
        self._speaking = True

    async def _on_speech_finished(self, event: SpeechFinished) -> None:
        self._speaking = False
        self._quiet_at = asyncio.get_running_loop().time() + AFTER_SPEECH_SECONDS
