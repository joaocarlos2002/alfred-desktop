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
