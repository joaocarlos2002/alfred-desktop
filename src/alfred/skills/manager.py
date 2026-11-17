"""Gerenciador de skills (#123): registro, habilitar/desabilitar em tempo real e isolamento."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from alfred.config.events import SettingsChanged
from alfred.core.commands import CommandRegistry
from alfred.core.event_bus import Subscription
from alfred.core.settings_service import SettingsService
from alfred.core.tools import ToolRegistry
from alfred.domain.module import Health, HealthStatus, Module
from alfred.skills.base import Skill

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillStatus:
    key: str
    name: str
    description: str
    enabled: bool
    active: bool
    problem: str | None = None


@dataclass
class _Active:
    tools: list[str] = field(default_factory=list)
    removers: list[Callable[[], None]] = field(default_factory=list)


class SkillManager(Module):
    name = "skills"
    depends_on = ("settings", "event_bus")
    optional = True

    def __init__(
        self, settings: SettingsService, tools: ToolRegistry, commands: CommandRegistry
    ) -> None:
        self._settings = settings
        self._tools = tools
        self._commands = commands
        self._skills: dict[str, Skill] = {}
        self._active: dict[str, _Active] = {}
        self.problems: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._subscription: Subscription[SettingsChanged] | None = None

    def register(self, skill: Skill) -> None:
        if skill.key in self._skills:
            raise ValueError(f"Skill duplicada: {skill.key}")
        self._skills[skill.key] = skill

    def get(self, key: str) -> Skill:
        return self._skills[key]

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        await self._sync()
        bus = self._settings.bus
        self._subscription = bus.subscribe(SettingsChanged, self._on_settings, name="skills")

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        async with self._lock:
            for key in list(self._active):
                await self._deactivate(key)

    async def health(self) -> Health:
        if self.problems:
            names = ", ".join(sorted(self.problems))
            return Health(status=HealthStatus.DEGRADED, detail=f"skills com falha: {names}")
        return Health(status=HealthStatus.HEALTHY, detail=f"{len(self._active)} skill(s) ativa(s)")

    # ------------------------------------------------------------------ estado
    def statuses(self) -> list[SkillStatus]:
        enabled = set(self._settings.current.skills.enabled)
        return [
            SkillStatus(
                key=key,
                name=skill.name,
                description=skill.description,
                enabled=key in enabled,
                active=key in self._active,
                problem=self.problems.get(key),
            )
            for key, skill in sorted(self._skills.items(), key=lambda kv: kv[1].name)
        ]

    async def set_enabled(self, key: str, enabled: bool) -> None:
        if key not in self._skills:
            raise KeyError(key)
        current = list(self._settings.current.skills.enabled)
        if enabled and key not in current:
            current.append(key)
        elif not enabled and key in current:
            current.remove(key)
        await self._settings.update("skills", enabled=tuple(current))
        await self._sync()  # imediato, sem esperar o evento

    async def _on_settings(self, event: SettingsChanged) -> None:
        if event.section == "skills":
            await self._sync()

    # ------------------------------------------------------------------ ativação
    async def _sync(self) -> None:
        wanted = set(self._settings.current.skills.enabled) & set(self._skills)
        async with self._lock:
            for key in [k for k in self._active if k not in wanted]:
                await self._deactivate(key)
            for key in sorted(wanted - set(self._active)):
                await self._activate(key)
            for key in list(self.problems):
                if key not in wanted:
                    del self.problems[key]

    async def _activate(self, key: str) -> None:
        skill = self._skills[key]
        record = _Active()
        try:
            await skill.activate()
            for tool in skill.tools():
                self._tools.register(tool)
                record.tools.append(tool.name)
            for pattern, handler in skill.commands():
                record.removers.append(self._commands.register(pattern, handler))
        except Exception as exc:  # isolamento: só esta skill fica de fora
            self._rollback(record)
            self.problems[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("Skill '%s' não pôde ser ativada: %s", key, exc)
            return
        self.problems.pop(key, None)
        self._active[key] = record
        logger.info("Skill '%s' ativada (%d ferramenta(s))", key, len(record.tools))

    async def _deactivate(self, key: str) -> None:
        record = self._active.pop(key)
        self._rollback(record)
        try:
            await self._skills[key].deactivate()
        except Exception:
            logger.exception("Falha ao desativar a skill '%s'", key)
        logger.info("Skill '%s' desativada", key)

    def _rollback(self, record: _Active) -> None:
        for name in record.tools:
            self._tools.unregister(name)
        for remove in record.removers:
            remove()
