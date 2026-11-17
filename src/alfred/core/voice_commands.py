"""Comandos de voz básicos, executados como tarefas reais (RF-004, RF-014).

Até a inteligência artificial (Fase 5), o ALFRED entende um conjunto pequeno e explícito de
comandos. Para o resto, responde com honestidade que ainda não sabe executar o pedido — nunca finge
ter feito algo. Cada fala vira uma tarefa visível no painel de atividades.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from alfred.core.commands import CommandRegistry
from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.module import Module
from alfred.domain.tasks import (
    StepStatus,
    TaskFinished,
    TaskStarted,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)
from alfred.domain.voice import SpeakRequested, StopSpeakingRequested, UtteranceTranscribed

logger = logging.getLogger(__name__)

WEEKDAYS = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]
MONTHS = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]
EMERGENCY_RE = re.compile(r"\b(pare tudo|cancel\w* tudo|emergencia)\b")
STOP_RE = re.compile(r"(pare|para|silencio|chega|cancela|cancelar)")
TIME_RE = re.compile(r"\b(que horas|horas sao|hora e agora)\b")
DATE_RE = re.compile(r"\b(que dia|data de hoje|dia e hoje)\b")
UNKNOWN_REPLY = (
    "Entendi: {text} Ainda não sei executar esse pedido. "
    "A inteligência artificial chega em uma próxima versão."
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def strip_wake_word(text: str) -> str:
    pattern = r"^\s*(ei|ola|olá|oi)?[\s,]*(alfred[oi]?|fred)\b[\s,.!?]*"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


def spoken_time(now: datetime) -> str:
    verb = "É" if now.hour == 1 else "São"
    hours = f"{now.hour} hora" + ("" if now.hour == 1 else "s")
    if now.minute == 0:
        return f"{verb} {hours} em ponto."
    minutes = f"{now.minute} minuto" + ("" if now.minute == 1 else "s")
    return f"{verb} {hours} e {minutes}."


def spoken_date(now: datetime) -> str:
    return f"Hoje é {WEEKDAYS[now.weekday()]}, {now.day} de {MONTHS[now.month - 1]} de {now.year}."


@dataclass(frozen=True)
class Outcome:
    command: str
    reply: str | None
    understood: bool = True
    tool: str | None = None


type Action = Callable[[], Awaitable[object]]


class VoiceCommandHandler(Module):
    name = "voice_commands"
    depends_on = ("event_bus", "emergency")

    def __init__(
        self,
        bus: EventBus,
        *,
        emergency: Action,
        clock: Callable[[], datetime] = datetime.now,
        registry: CommandRegistry | None = None,
        fallback: Callable[[str], object] | None = None,
    ) -> None:
        self.fallback = fallback
        self.registry = registry or CommandRegistry()
        self._bus = bus
        self._emergency = emergency
        self._clock = clock
        self._subscription: Subscription[UtteranceTranscribed] | None = None

    async def start(self) -> None:
        self._subscription = self._bus.subscribe(
            UtteranceTranscribed, self.handle, name="voice-commands"
        )

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()

    async def _is_builtin(self, text: str) -> bool:
        """Comandos atendidos sem IA (os mesmos padrões de ``interpret``)."""
        command = normalize(strip_wake_word(text))
        return bool(
            not command
            or EMERGENCY_RE.search(command)
            or STOP_RE.fullmatch(command)
            or TIME_RE.search(command)
            or DATE_RE.search(command)
            or self.registry.match(command)
        )

    async def interpret(self, text: str) -> Outcome:
        command = normalize(strip_wake_word(text))
        if not command:
            return Outcome("vazio", "Sim? Estou ouvindo.")
        if EMERGENCY_RE.search(command):
            await self._emergency()
            return Outcome("emergencia", "Tudo interrompido.")
        if STOP_RE.fullmatch(command):
            await self._bus.publish(StopSpeakingRequested())
            return Outcome("parar_fala", None)
        if TIME_RE.search(command):
            return Outcome("hora", spoken_time(self._clock()))
        if DATE_RE.search(command):
            return Outcome("data", spoken_date(self._clock()))
        registered = self.registry.match(command)
        if registered is not None:
            found, handler = registered
            result = await handler(found)
            return Outcome(result.tool, result.reply, understood=result.succeeded, tool=result.tool)
        return Outcome("desconhecido", UNKNOWN_REPLY.format(text=text.strip()), understood=False)

    async def handle(self, event: UtteranceTranscribed) -> None:
        if event.trigger == "dictation":
            return  # fala ditada vai para o texto, não para comandos nem para a IA
        if self.fallback is not None and not await self._is_builtin(event.text):
            # Pedido livre: o orquestrador cria a tarefa e responde (IA, Fase 5).
            self.fallback(event.text)
            return
        task_id = uuid4()
        await self._bus.publish(TaskStarted(task_id=task_id, title=event.text, origin="voice"))
        await self._bus.publish(
            TaskStepStarted(
                task_id=task_id,
                position=1,
                tool="interpretar_comando",
                params={"texto": event.text},
            )
        )
        try:
            outcome = await self.interpret(event.text)
        except Exception as exc:
            logger.exception("Falha ao executar comando de voz")
            await self._bus.publish(
                TaskStepFinished(
                    task_id=task_id, position=1, status=StepStatus.FAILED, error=str(exc)
                )
            )
            await self._bus.publish(
                TaskFinished(task_id=task_id, status=TaskStatus.FAILED, error=str(exc))
            )
            return
        await self._bus.publish(
            TaskStepFinished(
                task_id=task_id,
                position=1,
                status=StepStatus.SUCCEEDED if outcome.understood else StepStatus.FAILED,
                result={"comando": outcome.command},
                error=None if outcome.understood else outcome.reply or "comando não cumprido",
            )
        )
        if outcome.reply:
            await self._bus.publish(
                TaskStepStarted(
                    task_id=task_id,
                    position=2,
                    tool="responder_por_voz",
                    params={"texto": outcome.reply},
                )
            )
            await self._bus.publish(SpeakRequested(text=outcome.reply))
            await self._bus.publish(
                TaskStepFinished(task_id=task_id, position=2, status=StepStatus.SUCCEEDED)
            )
        await self._bus.publish(
            TaskFinished(
                task_id=task_id,
                status=TaskStatus.SUCCEEDED if outcome.understood else TaskStatus.FAILED,
                error=None if outcome.understood else outcome.reply or "Comando não cumprido",
            )
        )
