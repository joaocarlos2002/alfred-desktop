from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from alfred.ai.fake import FakeProvider
from alfred.ai.ollama import OllamaProvider
from alfred.ai.openai_compat import OpenAICompatibleProvider
from alfred.ai.providers import ProviderRegistry
from alfred.ai.vision import ExternalUploadDeniedError, ScreenAnalyzer, register_vision_tool
from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.core.tools import ToolRegistry
from alfred.domain.ai import ChatMessage, ProviderUnavailableError, ToolCall
from alfred.domain.confirmation import ConfirmationDecision

PNG = b"\x89PNG\r\n\x1a\nfake-image"


class Confirmations:
    def __init__(self, decision: ConfirmationDecision) -> None:
        self.decision = decision
        self.asked: list[dict[str, str]] = []

    async def request(self, **kwargs: str) -> ConfirmationDecision:
        self.asked.append(kwargs)
        return self.decision


class Screen:
    def __init__(self) -> None:
        self.captures: list[tuple[str, str | None]] = []

    async def capture(self, reason: str, window: str | None) -> bytes:
        self.captures.append((reason, window))
        return PNG


async def settings_for(tmp_path: Path, **ai: object) -> SettingsService:
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), EventBus())
    await settings.start()
    await settings.update("ai", **ai)
    return settings


def registry(*providers: Any) -> ProviderRegistry:
    reg = ProviderRegistry()
    for provider in providers:
        reg.register(provider)
    return reg


async def test_local_model_gets_image_and_uia_tree_without_confirmation(tmp_path: Path) -> None:
    provider = FakeProvider(replies=[ChatMessage(role="assistant", content="Há um botão Salvar.")])
    screen = Screen()
    confirmations = Confirmations(ConfirmationDecision.DENIED)

    async def elements(window: str) -> list[dict[str, Any]]:
        return [{"nome": "Salvar", "tipo": "Button"}]

    analyzer = ScreenAnalyzer(
        registry(provider),
        await settings_for(tmp_path, provider="fake", vision_model="llava"),
        confirmations,
        screen.capture,
        elements,
    )
    result = await analyzer.analyze("onde está o botão salvar?", window="Bloco de Notas")

    assert result == {
        "resposta": "Há um botão Salvar.",
        "provedor": "fake",
        "modelo": "llava",
        "local": True,
    }
    assert confirmations.asked == []  # local: nenhuma autorização de envio necessária
    assert screen.captures[0][1] == "Bloco de Notas"
    user = provider.calls[0]["messages"][1]
    assert user.images == (PNG,)
    assert '"Salvar"' in user.content


async def test_external_upload_requires_explicit_approval_every_time(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Uma planilha."}}]})

    external = OpenAICompatibleProvider(
        "externo",
        base_url=lambda: "https://api.exemplo.com/v1",
        credentials=type("C", (), {"get": lambda self, name: "sk-x"})(),
        network_enabled=lambda: True,
        transport=httpx.MockTransport(handler),
    )
    settings = await settings_for(tmp_path, vision_provider="externo", vision_model="gpt-vision")
    screen = Screen()

    denied = Confirmations(ConfirmationDecision.DENIED)
    analyzer = ScreenAnalyzer(registry(external), settings, denied, screen.capture)
    with pytest.raises(ExternalUploadDeniedError):
        await analyzer.analyze("o que há na tela?")
    assert sent == []
    assert screen.captures == []  # nem chega a capturar
    assert denied.asked[0]["category"] == "external_actions"

    approved = Confirmations(ConfirmationDecision.APPROVED)
    analyzer = ScreenAnalyzer(registry(external), settings, approved, screen.capture)
    await analyzer.analyze("o que há na tela?")
    await analyzer.analyze("e agora?")
    assert len(approved.asked) == 2  # a autorização nunca é lembrada
    image = sent[0]["messages"][1]["content"][1]
    assert image["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()


async def test_without_confirmation_service_external_is_denied(tmp_path: Path) -> None:
    external = FakeProvider("externo")
    external.local = False
    settings = await settings_for(tmp_path, provider="externo", vision_model="m")
    analyzer = ScreenAnalyzer(registry(external), settings, None, Screen().capture)
    with pytest.raises(ExternalUploadDeniedError):
        await analyzer.analyze("x")


async def test_no_vision_model_fails_before_capturing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}]})
        return httpx.Response(200, json={"capabilities": ["completion", "tools"]})

    ollama = OllamaProvider(transport=httpx.MockTransport(handler))
    screen = Screen()
    analyzer = ScreenAnalyzer(registry(ollama), await settings_for(tmp_path), None, screen.capture)
    with pytest.raises(ProviderUnavailableError, match="visão"):
        await analyzer.analyze("x")
    assert screen.captures == []


async def test_ollama_detects_vision_models_and_sends_base64_images() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2"}, {"name": "llava"}]})
        body = json.loads(request.content)
        if request.url.path == "/api/show":
            caps = ["completion", "vision"] if body["model"] == "llava" else ["completion"]
            return httpx.Response(200, json={"capabilities": caps})
        seen.append(body)
        return httpx.Response(200, json={"model": "llava", "message": {"content": "ok"}})

    ollama = OllamaProvider(transport=httpx.MockTransport(handler))
    assert await ollama.vision_models() == ["llava"]
    await ollama.chat([ChatMessage(role="user", content="?", images=(PNG,))], model="llava")
    assert seen[0]["messages"][0]["images"] == [base64.b64encode(PNG).decode()]


async def test_tool_is_registered_and_runs(tmp_path: Path) -> None:
    provider = FakeProvider(replies=[ChatMessage(role="assistant", content="Tela vazia.")])
    analyzer = ScreenAnalyzer(
        registry(provider),
        await settings_for(tmp_path, provider="fake", vision_model="m"),
        None,
        Screen().capture,
    )
    tools = ToolRegistry()
    register_vision_tool(tools, analyzer)
    tool, params = tools.validate(
        ToolCall(id="1", name="analisar_tela", arguments={"pergunta": "?"})
    )
    result = await tools.execute(ToolCall(id="1", name="analisar_tela"), params)
    assert tool.category == "screen"
    assert "Tela vazia." in json.dumps(result, ensure_ascii=False)


@pytest.mark.windows
async def test_real_local_vision_model_if_available() -> None:
    real = OllamaProvider()
    try:
        models = await real.vision_models()
    except ProviderUnavailableError:
        pytest.skip("Ollama não está instalado/em execução nesta máquina")
    if not models:
        pytest.skip("Nenhum modelo multimodal local instalado no Ollama")
    from alfred.automation.screen import _grab, monitor_areas

    png = _grab(monitor_areas()[1])
    response = await real.chat(
        [ChatMessage(role="user", content="Descreva a imagem em uma frase.", images=(png,))],
        model=models[0],
        timeout=300,
    )
    assert response.message.content.strip()
