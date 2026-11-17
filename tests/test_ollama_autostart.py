"""O Ollama local é iniciado sozinho quando está instalado mas parado (ex.: após reiniciar o PC)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from alfred.ai.ollama import OllamaProvider
from alfred.ai.providers import AIService, ProviderRegistry
from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.ai import ChatMessage


class FakeServer:
    """Recusa conexões até ser "iniciado" pelo launcher."""

    def __init__(self) -> None:
        self.running = False
        self.launches: list[list[str]] = []

    def launch(self, command: list[str]) -> None:
        self.launches.append(command)
        self.running = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.running:
            raise httpx.ConnectError("recusado")
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})
        return httpx.Response(
            200, json={"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "Oi!"}}
        )


def provider(server: FakeServer, exe: Path | None, base_url: str = "http://127.0.0.1:11434") -> Any:
    return OllamaProvider(
        base_url,
        transport=httpx.MockTransport(server.handler),
        executable=exe,
        launcher=server.launch,
        poll_interval=0.01,
    )


async def test_starts_installed_server_when_stopped(tmp_path: Path) -> None:
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    server = FakeServer()
    ollama = provider(server, exe)
    assert await ollama.ensure_running(timeout=2)
    assert server.launches == [[str(exe), "serve"]]
    assert await ollama.ensure_running(timeout=2)  # já rodando: não inicia de novo
    assert len(server.launches) == 1


async def test_never_launches_when_not_installed_or_not_local(tmp_path: Path) -> None:
    server = FakeServer()
    assert not await provider(server, None).ensure_running(timeout=0.2)
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    remote = provider(server, exe, base_url="http://192.168.0.10:11434")
    assert not await remote.ensure_running(timeout=0.2)  # nunca inicia nada para um servidor remoto
    assert server.launches == []


async def ai_service(tmp_path: Path, ollama: Any, **ai: Any) -> AIService:
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), EventBus())
    await settings.start()
    await settings.update("ai", provider="ollama", **ai)
    registry = ProviderRegistry()
    registry.register(ollama)
    return AIService(registry, settings, EventBus(), backoff=0.01)


async def test_request_after_reboot_starts_ollama_and_succeeds(tmp_path: Path) -> None:
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    server = FakeServer()
    service = await ai_service(tmp_path, provider(server, exe))
    reply = await service.chat([ChatMessage(role="user", content="oi")])
    assert reply.message.content == "Oi!"
    assert len(server.launches) == 1


async def test_autostart_can_be_disabled(tmp_path: Path) -> None:
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    server = FakeServer()
    service = await ai_service(tmp_path, provider(server, exe), start_local_server=False)
    await service.start()
    assert server.launches == []
