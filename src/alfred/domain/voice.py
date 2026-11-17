"""Eventos do módulo de voz."""

from __future__ import annotations

from alfred.domain.events import Event


class WakeWordDetected(Event):
    """A palavra de ativação foi reconhecida (e confirmada pela segunda etapa)."""

    word: str
    confidence: float


class VoiceModelStatus(Event):
    """Progresso de preparo de um modelo de voz (download/carga)."""

    model: str
    status: str  # downloading | ready | failed
    detail: str | None = None


class SpeechStarted(Event):
    """O ALFRED começou a falar."""

    text: str


class SpeechFinished(Event):
    text: str
    interrupted: bool


class UtteranceTranscribed(Event):
    """Uma fala do usuário foi transcrita (após a palavra de ativação ou o atalho)."""

    text: str
    audio_seconds: float
    latency_seconds: float
    trigger: str  # wake_word | hotkey


class SpeakRequested(Event):
    """Pede ao módulo de voz que fale ``text``."""

    text: str
    interrupt: bool = False


class StopSpeakingRequested(Event):
    pass


class DictationStarted(Event):
    """Modo ditado ativo: o que o usuário falar será digitado no aplicativo em foco."""


class DictationStopped(Event):
    characters: int = 0
