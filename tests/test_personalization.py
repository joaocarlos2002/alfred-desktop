"""Personalização (#121): perfil, hábitos e feedback falado moldam o ALFRED."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from alfred.ai.fake import FakeProvider
from alfred.app import bootstrap
from alfred.config.store import SettingsStore
from alfred.core.commands import CommandRegistry
from alfred.core.event_bus import EventBus
from alfred.core.orchestrator import AssistantReply
from alfred.core.settings_service import SettingsService
from alfred.domain.ai import ChatMessage
from alfred.domain.tasks import StepStatus, TaskStarted, TaskStatus
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.memory_repository import SqlMemoryRepository
from alfred.infrastructure.db.task_repository import SqlTaskRepository
from alfred.memory.personalization import PersonalizationService
from alfred.memory.service import MemoryService


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "alfred.db")
    await db.start()
    yield db
    await db.stop()


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


async def service(
    tmp_path: Path, database: Database, bus: EventBus
) -> tuple[PersonalizationService, SettingsService, MemoryService, CommandRegistry]:
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    memory = MemoryService(SqlMemoryRepository(database), bus)
    await memory.start()
    commands = CommandRegistry()
    personal = PersonalizationService(settings, bus, SqlTaskRepository(database), memory)
    personal.register_commands(commands)
    await personal.start()
    return personal, settings, memory, commands


async def say(commands: CommandRegistry, text: str) -> str:
    found = commands.match(text)
    assert found is not None, text
    match, handler = found
    return (await handler(match)).reply


async def test_profile_becomes_instructions(
    tmp_path: Path, database: Database, bus: EventBus
) -> None:
    personal, settings, _, _ = await service(tmp_path, database, bus)
    assert await personal.context() == ""  # perfil automático: nada a acrescentar
    await settings.update("profile", response_length="short", tone="formal", address="senhor")
    context = await personal.context()
    assert "curtas" in context
    assert "formal" in context
    assert "senhor" in context
    await personal.reset()
    assert await personal.context() == ""  # redefinir remove o efeito na hora


async def test_spoken_feedback_adjusts_the_profile(
    tmp_path: Path, database: Database, bus: EventBus
) -> None:
    _personal, settings, _, commands = await service(tmp_path, database, bus)
    await say(commands, "respostas mais curtas")
    assert settings.current.profile.response_length == "short"
    await say(commands, "pode detalhar mais")
    assert settings.current.profile.response_length == "detailed"
    await say(commands, "seja mais informal")
    assert settings.current.profile.tone == "casual"
    reply = await say(commands, "me chame de joao")
    assert settings.current.profile.address == "Joao"
    assert "Joao" in reply
    await say(commands, "redefinir meu perfil")
    assert settings.current.profile.response_length == "auto"
    assert settings.current.profile.address is None


async def test_dont_do_that_again_records_a_preference_about_the_last_request(
    tmp_path: Path, database: Database, bus: EventBus
) -> None:
    _personal, _, memory, commands = await service(tmp_path, database, bus)
    task = uuid4()
    await bus.publish(
        TaskStarted(task_id=task, title="abra o Spotify quando eu chegar", origin="voice")
    )
    await bus.publish(AssistantReply(task_id=task, text="Abri o Spotify."))
    await bus.drain(timeout=1)
    await say(commands, "nao faca mais isso")
    [learned] = await memory.memories()
    assert learned.kind == "preference"
    assert "abra o Spotify quando eu chegar" in learned.content
    assert "ótimo" in (await say(commands, "assim esta otimo")).lower()


async def test_habits_come_from_real_task_history(
    tmp_path: Path, database: Database, bus: EventBus
) -> None:
    repo = SqlTaskRepository(database)
    now = datetime.now(UTC)
    for name in ("Calculadora", "Calculadora", "Word", "Calculadora", "Word", "Paint"):
        task = uuid4()
        await repo.task_started(
            task_id=task, correlation_id=uuid4(), title=f"abra {name}", origin="voice", at=now
        )
        await repo.step_started(
            task_id=task, position=1, tool="abrir_aplicativo", params={"nome": name}, at=now
        )
        await repo.step_finished(
            task_id=task, position=1, status=StepStatus.SUCCEEDED, result={}, error=None, at=now
        )
        await repo.task_finished(task_id=task, status=TaskStatus.SUCCEEDED, error=None, at=now)
    personal, settings, _, _ = await service(tmp_path, database, bus)
    assert await personal.habits() == ["Calculadora", "Word"]  # Paint só uma vez: não é hábito
    assert "Calculadora" in await personal.context()
    await settings.update("profile", use_habits=False)
    assert "Calculadora" not in await personal.context()


async def test_learned_preference_changes_the_instruction_sent_to_the_model(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(replies=[ChatMessage(role="assistant", content="Ok.")] * 2)
    app, _ = bootstrap(tmp_path, ai_providers=(provider,))
    await app.manager.start_all()
    try:
        assert app.settings is not None
        assert app.orchestrator is not None
        await app.settings.update("ai", provider="fake", learning_mode="off")
        await app.orchestrator.run("oi", origin="ui")
        before = provider.calls[0]["messages"][0].content
        await app.settings.update("profile", response_length="short")
        await app.orchestrator.run("oi de novo", origin="ui")
        after = provider.calls[1]["messages"][0].content
        assert "curtas" not in before
        assert "curtas" in after
    finally:
        await app.manager.stop_all()


def test_feedback_patterns_are_specific() -> None:
    from alfred.memory.personalization import FEEDBACK_PATTERNS

    for pattern in FEEDBACK_PATTERNS:
        assert not re.fullmatch(pattern, "abra o bloco de notas", re.IGNORECASE)


def test_profile_page_resets_from_the_settings_window(tmp_path: Path) -> None:
    from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
    from alfred.ui.settings_window import ProfilePage, SettingsWindow

    ensure_qt_application()
    app, _ = bootstrap(tmp_path)

    async def scenario() -> str:
        import asyncio

        await app.manager.start_all()
        assert app.settings is not None
        await app.settings.update("profile", response_length="short", address="senhor")
        window = SettingsWindow(app.settings)
        page = window.findChild(ProfilePage)
        assert page is not None
        page.reset_button.click()
        await asyncio.sleep(0.1)
        length = app.settings.current.profile.response_length
        await app.manager.stop_all()
        return length

    assert run_on_qt(scenario()) == "auto"
