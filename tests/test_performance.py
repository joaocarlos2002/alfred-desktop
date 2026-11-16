"""Guardas de desempenho (Seção 9, issue #47): carregamento sob demanda e inicialização rápida."""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import httpx

from alfred.ai.fake import FakeProvider
from alfred.ai.ollama import OllamaProvider
from alfred.ai.providers import AIService, ProviderRegistry
from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService

HEAVY = (
    "numpy",
    "faster_whisper",
    "ctranslate2",
    "vosk",
    "onnxruntime",
    "piper",
    "pywinauto",
    "playwright",
    "mss",
    "httpx",
    "PySide6.QtWidgets",
)


def test_entry_point_does_not_import_heavy_modules() -> None:
    """Modelos, automação, rede e Qt só carregam quando o recurso é usado."""
    code = (
        "import json, sys, alfred.__main__; "
        f"print(json.dumps([m for m in {HEAVY!r} if m in sys.modules]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


class SlowCatalog:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.refreshed = False
        self.aliases: dict[str, list[str]] = {}

    def refresh(self) -> None:
        self.release.wait(5)
        self.refreshed = True

    def resolve(self, query: str) -> Any:
        assert self.refreshed, "resolve antes da lista de aplicativos"
        raise LookupError(query)


async def test_app_catalog_loads_in_background_and_requests_wait_for_it() -> None:
    from alfred.automation.service import AppsService
    from alfred.core.action_log import ActionLog

    class Activity:
        async def add(self, entry: Any) -> Any:
            return entry

    catalog = SlowCatalog()
    service = AppsService(catalog, ActionLog(Activity()), None)  # type: ignore[arg-type]
    await asyncio.wait_for(service.start(), 0.5)  # não espera o Get-StartApps
    assert not catalog.refreshed
    pending = asyncio.create_task(service.open_app("calculadora"))
    await asyncio.sleep(0.05)
    assert not pending.done()  # o pedido aguarda a lista
    catalog.release.set()
    # o catálogo de teste não tem apps; importa que resolve veio depois da lista
    with contextlib.suppress(LookupError):
        await asyncio.wait_for(pending, 5)
    assert catalog.refreshed
    await service.stop()


async def ai_service(tmp_path: Path, *providers: Any, provider: str) -> AIService:
    bus = EventBus()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    await settings.update("ai", provider=provider)
    registry = ProviderRegistry()
    for item in providers:
        registry.register(item)
    return AIService(registry, settings, bus)


async def test_local_model_is_preloaded_when_listening_starts(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})
        requests.append({"path": request.url.path, **json.loads(request.content)})
        return httpx.Response(200, json={"done": True})

    ollama = OllamaProvider(transport=httpx.MockTransport(handler))
    service = await ai_service(tmp_path, ollama, provider="ollama")
    await service.warm_up()
    assert requests == [{"path": "/api/generate", "model": "qwen2.5:7b", "keep_alive": "5m"}]


async def test_preload_never_touches_external_providers_or_raises(tmp_path: Path) -> None:
    external = FakeProvider("externo")
    external.local = False
    service = await ai_service(tmp_path, external, provider="externo")
    await service.warm_up()  # externo: nada é enviado só por ter ouvido a palavra de ativação
    assert external.calls == []

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado")

    down = OllamaProvider(transport=httpx.MockTransport(offline))
    service = await ai_service(tmp_path, down, provider="ollama")
    await service.warm_up()  # Ollama fora do ar: silencioso; o pedido real explica o erro
