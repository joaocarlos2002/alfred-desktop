"""Personalização (#121): o que o ALFRED sabe de você muda como ele se comporta.

- **Perfil** (``settings.profile``): tamanho das respostas, tom e como chamar o usuário.
- **Hábitos**: aplicativos que o usuário abre com frequência (do histórico real de tarefas),
  sugeridos ao planejador — desligável.
- **Feedback falado**: "respostas mais curtas", "pode detalhar mais", "seja mais formal/informal",
  "me chame de X" ajustam o perfil; "não faça mais isso" vira uma preferência sobre o último
  pedido; "redefinir meu perfil" volta ao padrão.

Tudo entra na instrução da IA pelo contexto do orquestrador e some na hora ao redefinir.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from alfred.config.settings import ProfileSettings
from alfred.core.commands import CommandRegistry, CommandResult
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.orchestrator import AssistantReply
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Module
from alfred.domain.task_history import TaskRecord
from alfred.domain.tasks import StepStatus, TaskStarted
from alfred.memory.service import MemoryService

logger = logging.getLogger(__name__)

HABIT_WINDOW = timedelta(days=30)
HABIT_MIN_USES = 2
HABIT_LIMIT = 5

LENGTH = {
    "short": "Prefira respostas curtas e diretas (uma ou duas frases).",
    "detailed": "Responda com mais detalhes e explicações quando for útil.",
}
TONE = {
    "formal": "Use um tom formal e cortês.",
    "casual": "Use um tom descontraído e próximo.",
}

# Texto já normalizado pelo tratador de voz (minúsculas, sem acentos).
P = r"(?:alfred\s+)?"
SHORTER = P + r"(?:respostas? mais curtas?|fale menos|seja mais (?:breve|direto|objetivo))"
LONGER = P + r"(?:pode detalhar mais|explique melhor|respostas? mais (?:longas?|detalhadas?))"
FORMAL = P + r"seja mais formal"
CASUAL = P + r"seja mais (?:informal|descontraido)"
ADDRESS = P + r"me chame de (?P<name>[\w ]{2,40})"
DONT = P + r"(?:nao faca mais isso|nao faca isso de novo|nunca mais faca isso)"
GOOD = P + r"(?:assim esta otimo|gostei assim|perfeito assim)"
RESET = P + r"(?:redefinir|resetar|zerar) (?:o |meu )?perfil"
FEEDBACK_PATTERNS = (SHORTER, LONGER, FORMAL, CASUAL, ADDRESS, DONT, GOOD, RESET)


class TaskHistory(Protocol):
    async def list_tasks(
        self, *, status: Any = None, since: datetime | None = None, limit: int = 100
    ) -> list[TaskRecord]: ...


class PersonalizationService(Module):
    name = "personalization"
    depends_on = ("settings", "database", "event_bus")
    optional = True

    def __init__(
        self,
        settings: SettingsService,
        bus: EventBus,
        history: TaskHistory,
        memory: MemoryService,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._history = history
        self._memory = memory
        self._requests: dict[UUID, str] = {}
        self._last_request: str | None = None
        self._subscriptions: list[Subscription[Any]] = []

    async def start(self) -> None:
        self._subscriptions = [
            self._bus.subscribe(TaskStarted, self._on_task, name="personalization-requests"),
            self._bus.subscribe(AssistantReply, self._on_reply, name="personalization-replies"),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()

    @property
    def profile(self) -> ProfileSettings:
        return self._settings.current.profile

    # ------------------------------------------------------------------ contexto da IA
    async def habits(self) -> list[str]:
        since = datetime.now(UTC) - HABIT_WINDOW
        counts: Counter[str] = Counter()
        for task in await self._history.list_tasks(since=since, limit=500):
            for step in task.steps:
                name = step.params.get("nome")
                if step.tool == "abrir_aplicativo" and step.status is StepStatus.SUCCEEDED and name:
                    counts[str(name)] += 1
        return [name for name, uses in counts.most_common(HABIT_LIMIT) if uses >= HABIT_MIN_USES]

    async def context(self) -> str:
        profile = self.profile
        lines: list[str] = []
        if profile.response_length in LENGTH:
            lines.append(LENGTH[profile.response_length])
        if profile.tone in TONE:
            lines.append(TONE[profile.tone])
        if profile.address:
            lines.append(f'Chame o usuário de "{profile.address}".')
        if profile.use_habits:
            habits = await self.habits()
            if habits:
                lines.append(
                    "Aplicativos que o usuário usa com frequência: " + ", ".join(habits) + "."
                )
        if not lines:
            return ""
        bullets = "\n".join(f"- {line}" for line in lines)
        return "Perfil do usuário (siga estas preferências):\n" + bullets

    async def reset(self) -> None:
        await self._settings.update("profile", **ProfileSettings().model_dump())
        logger.info("Perfil do usuário redefinido")

    # ------------------------------------------------------------------ feedback falado
    async def _on_task(self, event: TaskStarted) -> None:
        if event.origin in ("voice", "ui"):
            self._requests[event.task_id] = event.title

    async def _on_reply(self, event: AssistantReply) -> None:
        request = self._requests.pop(event.task_id, None)
        if request is not None:
            self._last_request = request

    def register_commands(self, registry: CommandRegistry) -> None:
        async def set_profile(reply: str, **changes: Any) -> CommandResult:
            await self._settings.update("profile", **changes)
            return CommandResult(reply, tool="perfil")

        async def shorter(_: re.Match[str]) -> CommandResult:
            return await set_profile("Certo, vou ser mais breve.", response_length="short")

        async def longer(_: re.Match[str]) -> CommandResult:
            return await set_profile(
                "Combinado, vou explicar com mais detalhes.", response_length="detailed"
            )

        async def formal(_: re.Match[str]) -> CommandResult:
            return await set_profile("Pois não. Serei mais formal.", tone="formal")

        async def casual(_: re.Match[str]) -> CommandResult:
            return await set_profile("Beleza, vou falar de um jeito mais leve.", tone="casual")

        async def address(match: re.Match[str]) -> CommandResult:
            name = " ".join(match.group("name").split()).title()
            return await set_profile(f"Certo, {name}.", address=name)

        async def dont(_: re.Match[str]) -> CommandResult:
            if self._last_request is None:
                return CommandResult(
                    "Não sei a que você se refere.", succeeded=False, tool="perfil"
                )
            await self._memory.remember(
                f'O usuário não quer que eu repita isto: "{self._last_request}".',
                kind="preference",
                source="feedback",
            )
            return CommandResult("Entendido, não vou fazer isso de novo.", tool="perfil")

        async def good(_: re.Match[str]) -> CommandResult:
            return CommandResult("Ótimo, vou continuar assim.", tool="perfil")

        async def reset(_: re.Match[str]) -> CommandResult:
            await self.reset()
            return CommandResult("Perfil redefinido para o padrão.", tool="perfil")

        for pattern, handler in (
            (SHORTER, shorter),
            (LONGER, longer),
            (FORMAL, formal),
            (CASUAL, casual),
            (ADDRESS, address),
            (DONT, dont),
            (GOOD, good),
            (RESET, reset),
        ):
            registry.register(pattern, handler)
