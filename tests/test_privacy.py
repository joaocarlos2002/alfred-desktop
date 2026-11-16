"""Auditoria de privacidade (Seção 9, issue #46)."""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from alfred.ai.openai_compat import OpenAICompatibleProvider
from alfred.ai.providers import ProviderRegistry
from alfred.ai.vision import ExternalUploadDeniedError, ScreenAnalyzer
from alfred.app import bootstrap, provider_locality
from alfred.config.settings import Settings
from alfred.core.privacy import NETWORK_FLOWS, data_flows
from alfred.domain.confirmation import ConfirmationDecision
from alfred.domain.voice import SpeakRequested, UtteranceTranscribed

SRC = Path(__file__).resolve().parents[1] / "src"
NETWORK_API = re.compile(
    r"^\s*(import|from)\s+(httpx|requests|urllib\.request|socket|aiohttp|websockets?|"
    r"playwright|faster_whisper|huggingface_hub)\b|urllib\.request|urlopen\(",
    re.MULTILINE,
)


def test_every_network_capable_module_is_in_the_inventory() -> None:
    found = {
        path.relative_to(SRC).as_posix()
        for path in (SRC / "alfred").rglob("*.py")
        if NETWORK_API.search(path.read_text("utf-8"))
    }
    documented = {flow.module for flow in NETWORK_FLOWS}
    assert found == documented, (
        "Saída de rede nova ou removida: atualize NETWORK_FLOWS (core/privacy.py) e "
        "docs/security/network-inventory.md"
    )
    doc = (SRC.parent / "docs" / "security" / "network-inventory.md").read_text("utf-8")
    for flow in NETWORK_FLOWS:
        assert flow.module in doc


def test_flow_states_follow_the_settings() -> None:
    defaults = Settings()
    flows = {f.label: f for f in data_flows(defaults, {"ollama": True, "externo": False})}
    assert not any(f.leaves_computer for f in flows.values())  # padrão: nada sai
    assert flows["Inteligência artificial"].state == "ollama: local"
    assert flows["Microfone"].state == "desligado"

    external_off = defaults.model_copy(
        update={"ai": defaults.ai.model_copy(update={"provider": "externo"})}
    )
    blocked = {f.label: f for f in data_flows(external_off, {"externo": False})}
    assert "bloqueado (rede desligada)" in blocked["Inteligência artificial"].state
    assert not blocked["Inteligência artificial"].leaves_computer

    external_on = external_off.model_copy(
        update={"privacy": external_off.privacy.model_copy(update={"network_enabled": True})}
    )
    active = {f.label: f for f in data_flows(external_on, {"externo": False})}
    assert active["Inteligência artificial"].leaves_computer
    assert active["Rede para serviços externos"].leaves_computer


# ---------------------------------------------------------------------- sentinela de rede
class NetworkSentinel:
    def __init__(self) -> None:
        self.attempts: list[str] = []


def _is_loopback(host: Any) -> bool:
    if host in ("localhost", None, ""):
        return True
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


@pytest.fixture
def sentinel(monkeypatch: pytest.MonkeyPatch) -> NetworkSentinel:
    """Bloqueia e registra qualquer conexão que não seja para este computador."""
    watch = NetworkSentinel()
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_loopback(host):
            watch.attempts.append(f"dns:{host}")
            raise OSError("rede bloqueada pelo teste")
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if not _is_loopback(host):
            watch.attempts.append(f"connect:{host}")
            raise OSError("rede bloqueada pelo teste")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    return watch


class Credentials:
    def get(self, name: str) -> str | None:
        return "sk-teste-0123456789abcdefghij"


class DenyAll:
    def __init__(self) -> None:
        self.asked = 0

    async def request(self, **kwargs: Any) -> ConfirmationDecision:
        self.asked += 1
        return ConfirmationDecision.DENIED


async def test_nothing_leaves_the_computer_with_defaults(
    tmp_path: Path, sentinel: NetworkSentinel
) -> None:
    """App real, provedor externo cadastrado e escolhido, rede desligada (padrão)."""
    app, _ = bootstrap(tmp_path, credentials=Credentials())
    seen: list[str] = []

    async def spoken(event: SpeakRequested) -> None:
        seen.append(event.text)

    app.bus.subscribe(SpeakRequested, spoken)
    await app.manager.start_all()
    assert app.settings is not None
    assert app.orchestrator is not None
    try:
        await app.settings.update("ai", provider="externo")
        reply = await app.orchestrator.run("resuma meus e-mails", origin="voice")
        assert "rede desligado" in reply  # recusa honesta, sem conexão
        await app.bus.publish(
            UtteranceTranscribed(
                text="Alfred, que horas são?",
                audio_seconds=1,
                latency_seconds=0.1,
                trigger="wake_word",
            )
        )
        await app.bus.drain(timeout=2)
        assert seen  # função essencial respondeu
    finally:
        await app.manager.stop_all()
    assert sentinel.attempts == []


async def test_screen_capture_is_never_sent_without_permission(
    tmp_path: Path, sentinel: NetworkSentinel
) -> None:
    from alfred.config.store import SettingsStore
    from alfred.core.event_bus import EventBus
    from alfred.core.settings_service import SettingsService

    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), EventBus())
    await settings.start()
    await settings.update("ai", vision_provider="externo", vision_model="m")
    registry = ProviderRegistry()
    registry.register(
        OpenAICompatibleProvider(
            "externo",
            base_url=lambda: "https://api.exemplo.com/v1",
            credentials=Credentials(),
            network_enabled=lambda: settings.current.privacy.network_enabled,
        )
    )
    captured: list[str] = []

    async def capture(reason: str, window: str | None) -> bytes:
        captured.append(reason)
        return b"\x89PNG"

    confirmations = DenyAll()
    analyzer = ScreenAnalyzer(registry, settings, confirmations, capture)
    # rede ligada, mas o usuário não autoriza o envio: nem captura nem conexão
    await settings.update("privacy", network_enabled=True)
    with pytest.raises(ExternalUploadDeniedError):
        await analyzer.analyze("o que há na tela?")
    assert confirmations.asked == 1
    assert captured == []
    assert sentinel.attempts == []


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    from alfred.ui.qt_runtime import ensure_qt_application

    return ensure_qt_application()


def test_privacy_section_shows_flow_states(qt_app: QApplication, tmp_path: Path) -> None:
    from alfred.ui.settings_window import SettingsWindow

    app, _ = bootstrap(tmp_path, credentials=Credentials())
    assert app.settings is not None
    ai = app.ai
    assert ai is not None
    window = SettingsWindow(app.settings, ai_providers=lambda: provider_locality(ai.registry))
    texts = [window.privacy.list.item(i).text() for i in range(window.privacy.list.count())]
    assert any(t.startswith("Rede para serviços externos: desligada") for t in texts)
    assert window.privacy.summary.text() == "Nada sai deste computador agora."
    assert "privacy" in window.forms  # o formulário continua editável
