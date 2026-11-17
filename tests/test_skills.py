"""Módulo de skills (#123): contrato, registro, habilitar/desabilitar e isolamento."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from alfred.config.store import SettingsStore
from alfred.core.commands import CommandRegistry, CommandResult
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.core.tools import Tool, ToolNotAllowedError, ToolRegistry
from alfred.skills.base import Skill
from alfred.skills.manager import SkillManager


class EchoParams(BaseModel):
    texto: str


class EchoSkill(Skill):
    key: ClassVar[str] = "eco"
    name: ClassVar[str] = "Eco"
    description: ClassVar[str] = "Repete o que você disser"

    def __init__(self) -> None:
        self.active = False

    def tools(self) -> list[Tool]:
        async def echo(p: EchoParams) -> dict[str, Any]:
            return {"eco": p.texto}

        return [Tool("ecoar", "Repete um texto", EchoParams, echo, "skills")]

    def commands(self) -> list[tuple[str, Any]]:
        async def handler(match: re.Match[str]) -> CommandResult:
            return CommandResult("eco!", tool="eco")

        return [(r"(?:alfred\s+)?modo eco", handler)]

    async def activate(self) -> None:
        self.active = True

    async def deactivate(self) -> None:
        self.active = False


class BrokenSkill(Skill):
    key: ClassVar[str] = "quebrada"
    name: ClassVar[str] = "Quebrada"
    description: ClassVar[str] = "Falha ao ativar"

    async def activate(self) -> None:
        raise RuntimeError("dispositivo indisponível")


@pytest.fixture
async def settings(tmp_path: Path) -> AsyncIterator[SettingsService]:
    bus = EventBus()
    await bus.start()
    service = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await service.start()
    yield service
    await bus.stop()


async def manager(
    settings: SettingsService, *skills: Skill
) -> tuple[SkillManager, ToolRegistry, CommandRegistry]:
    tools, commands = ToolRegistry(), CommandRegistry()
    mgr = SkillManager(settings, tools, commands)
    for skill in skills:
        mgr.register(skill)
    await mgr.start()
    return mgr, tools, commands


async def test_enabled_skill_contributes_tools_and_phrases(settings: SettingsService) -> None:
    await settings.update("skills", enabled=("eco",))
    echo = EchoSkill()
    mgr, tools, commands = await manager(settings, echo)
    assert echo.active
    assert tools.get("ecoar").category == "skills"
    assert commands.match("modo eco") is not None
    [status] = mgr.statuses()
    assert (status.key, status.enabled, status.active, status.problem) == ("eco", True, True, None)
    await mgr.stop()
    assert not echo.active


async def test_disabling_removes_tools_and_phrases_immediately(settings: SettingsService) -> None:
    await settings.update("skills", enabled=("eco",))
    echo = EchoSkill()
    mgr, tools, commands = await manager(settings, echo)
    await mgr.set_enabled("eco", False)
    assert settings.current.skills.enabled == ()
    with pytest.raises(ToolNotAllowedError):
        tools.get("ecoar")
    assert commands.match("modo eco") is None
    assert not echo.active
    await mgr.set_enabled("eco", True)  # e volta sem reiniciar
    assert tools.get("ecoar") is not None
    await mgr.stop()


async def test_failing_skill_is_isolated(settings: SettingsService) -> None:
    await settings.update("skills", enabled=("eco", "quebrada"))
    mgr, tools, _ = await manager(settings, BrokenSkill(), EchoSkill())
    statuses = {s.key: s for s in mgr.statuses()}
    assert statuses["quebrada"].active is False
    assert "dispositivo indisponível" in (statuses["quebrada"].problem or "")
    assert statuses["eco"].active  # as outras continuam funcionando
    assert tools.get("ecoar") is not None
    await mgr.stop()


async def test_disabled_by_default_unless_listed(settings: SettingsService) -> None:
    await settings.update("skills", enabled=())
    echo = EchoSkill()
    mgr, tools, _ = await manager(settings, echo)
    assert not echo.active
    assert not tools.allowed("ecoar")
    await mgr.stop()


async def test_skill_tools_still_go_through_the_registry_allowlist() -> None:
    tools = ToolRegistry()
    tool = EchoSkill().tools()[0]
    tools.register(tool)
    tools.unregister("ecoar")
    assert not tools.allowed("ecoar")
    tools.register(tool)  # pode voltar a ser registrada
    assert tools.allowed("ecoar")
