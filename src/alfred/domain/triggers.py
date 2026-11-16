"""Contrato de gatilhos: fonte, condição e ação (RF-011).

Um gatilho nunca executa nada diretamente: a ação é um pedido em linguagem natural entregue ao
orquestrador, que passa pelas mesmas ferramentas, permissões e confirmações de um pedido falado.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alfred.domain.events import Event

TriggerKind = Literal["voice", "hotkey", "schedule", "startup", "app_open", "system"]

KIND_LABELS: dict[str, str] = {
    "voice": "Frase de voz",
    "hotkey": "Atalho de teclado",
    "schedule": "Horário (cron)",
    "startup": "Ao iniciar o ALFRED",
    "app_open": "Abertura de aplicativo",
    "system": "Evento do sistema",
}

SYSTEM_EVENTS: dict[str, str] = {
    "energia_conectada": "Carregador conectado",
    "energia_desconectada": "Carregador desconectado",
    "bateria_baixa": "Bateria abaixo do limite",
}

_HOURS = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


class TriggerCondition(BaseModel):
    """Condições extras avaliadas no momento do disparo."""

    model_config = ConfigDict(frozen=True)

    hours: str | None = None
    """Janela de horário "HH:MM-HH:MM" (pode atravessar a meia-noite)."""
    weekdays: tuple[int, ...] = ()
    """Dias permitidos (0 = segunda ... 6 = domingo). Vazio: todos."""

    @field_validator("hours")
    @classmethod
    def _valid_hours(cls, value: str | None) -> str | None:
        if value and not _HOURS.match(value):
            raise ValueError('Use o formato "HH:MM-HH:MM"')
        return value or None

    @field_validator("weekdays")
    @classmethod
    def _valid_days(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(not 0 <= d <= 6 for d in value):
            raise ValueError("Dias da semana vão de 0 (segunda) a 6 (domingo)")
        return tuple(sorted(set(value)))

    def allows(self, now: datetime) -> bool:
        if self.weekdays and now.weekday() not in self.weekdays:
            return False
        if self.hours:
            m = _HOURS.match(self.hours)
            assert m is not None
            start = time(int(m[1]), int(m[2]))
            end = time(int(m[3]), int(m[4]))
            current = now.time().replace(second=0, microsecond=0)
            if start <= end:
                return start <= current <= end
            return current >= start or current <= end
        return True


class TriggerSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    kind: TriggerKind
    config: dict[str, Any] = Field(default_factory=dict)
    """voice: {"frase"}; hotkey: {"atalho"}; schedule: {"cron"}; app_open: {"app"};
    system: {"evento", "limite"?}; startup: {}."""
    condition: TriggerCondition = Field(default_factory=TriggerCondition)
    request: str = Field(min_length=1)
    """Pedido entregue ao orquestrador quando o gatilho dispara."""
    enabled: bool = True

    def source_value(self) -> str:
        key = {
            "voice": "frase",
            "hotkey": "atalho",
            "schedule": "cron",
            "app_open": "app",
            "system": "evento",
        }.get(self.kind)
        return str(self.config.get(key, "")) if key else ""


class TriggerFired(Event):
    trigger_id: int
    name: str
    kind: str
    detail: str = ""


class TriggerBlocked(Event):
    """Disparo barrado por uma proteção (ciclo, frequência, cadeia, falhas seguidas)."""

    trigger_id: int
    name: str
    reason: str


class TriggerSkipped(Event):
    trigger_id: int
    name: str
    reason: str
