from __future__ import annotations

from pathlib import Path

import pytest

from alfred.ai.fake import FakeProvider
from alfred.ai.providers import AIService, ProviderRegistry
from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.ai import (
    ChatMessage,
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ToolCall,
    ToolSpec,
)

HELLO = [ChatMessage(role="user", content="Olá")]


async def make(tmp_path: Path, *providers: FakeProvider, **ai: object) -> AIService:
    bus = EventBus()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    await settings.update("ai", provider="fake", **ai)
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return AIService(registry, settings, bus, backoff=0.01)


async def test_chat_uses_selected_provider_and_default_model(tmp_path: Path) -> None:
    provider = FakeProvider(
        replies=[ChatMessage(role="assistant", content="Olá! Em que posso ajudar?")]
    )
    service = await make(tmp_path, provider)

    response = await service.chat(HELLO)

    assert response.message.content == "Olá! Em que posso ajudar?"
    assert provider.calls[0]["model"] == "fake-model"
    assert service.total_output_tokens == len("Olá! Em que posso ajudar?")


async def test_tool_calls_are_returned_not_executed(tmp_path: Path) -> None:
    call = ToolCall(id="1", name="abrir_aplicativo", arguments={"nome": "calculadora"})
    provider = FakeProvider(replies=[ChatMessage(role="assistant", tool_calls=(call,))])
    service = await make(tmp_path, provider)
    tool = ToolSpec(
        name="abrir_aplicativo", description="Abre um app", parameters={"type": "object"}
    )

    response = await service.chat(HELLO, tools=[tool])

    assert response.tool_calls == (call,)
    assert provider.calls[0]["tools"] == [tool]


async def test_transient_errors_are_retried(tmp_path: Path) -> None:
    provider = FakeProvider(
        replies=[
            ProviderUnavailableError("conexão recusada"),
            ProviderTimeoutError("lento"),
            ChatMessage(role="assistant", content="ok"),
        ]
    )
    service = await make(tmp_path, provider, max_retries=2)
    assert (await service.chat(HELLO)).message.content == "ok"
    assert len(provider.calls) == 3


async def test_retries_are_bounded_and_auth_errors_are_not_retried(tmp_path: Path) -> None:
    flaky = FakeProvider(replies=[ProviderUnavailableError("fora")] * 5)
    service = await make(tmp_path, flaky, max_retries=1)
    with pytest.raises(ProviderUnavailableError):
        await service.chat(HELLO)
    assert len(flaky.calls) == 2

    auth = FakeProvider(
        replies=[ProviderAuthError("chave inválida"), ChatMessage(role="assistant")]
    )
    service = await make(tmp_path, auth)
    with pytest.raises(ProviderAuthError):
        await service.chat(HELLO)
    assert len(auth.calls) == 1


async def test_missing_provider_is_a_clear_error_and_degrades_health(tmp_path: Path) -> None:
    service = await make(tmp_path)
    with pytest.raises(ProviderUnavailableError, match="fake"):
        await service.chat(HELLO)
    assert (await service.health()).status == "degraded"


async def test_switching_provider_needs_no_orchestrator_change(tmp_path: Path) -> None:
    a = FakeProvider("a", [ChatMessage(role="assistant", content="de A")])
    b = FakeProvider("b", [ChatMessage(role="assistant", content="de B")])
    service = await make(tmp_path, a, b)
    await service._settings.update("ai", provider="a")
    assert (await service.chat(HELLO)).message.content == "de A"
    await service._settings.update("ai", provider="b")
    assert (await service.chat(HELLO)).message.content == "de B"
