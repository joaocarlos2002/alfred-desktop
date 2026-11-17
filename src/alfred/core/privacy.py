"""Inventário de saídas de rede e estado dos fluxos de dados (Seção 9, issue #46).

``NETWORK_FLOWS`` lista **todo** módulo que pode abrir conexões de rede. Um teste varre o código e
falha se aparecer uma saída nova que não esteja aqui — e, portanto, na seção Privacidade e em
``docs/security/network-inventory.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from alfred.config.settings import Settings


@dataclass(frozen=True)
class NetworkFlow:
    key: str
    module: str
    """Caminho do módulo a partir de ``src/``."""
    label: str
    destination: str
    sends: str
    leaves_computer: bool
    gate: str
    """O que controla esta saída."""


NETWORK_FLOWS: tuple[NetworkFlow, ...] = (
    NetworkFlow(
        "ollama",
        "alfred/ai/ollama.py",
        "IA local (Ollama)",
        "127.0.0.1:11434 — este computador",
        "pedidos, contexto e memórias; imagens de tela quando você pede uma análise",
        False,
        "não sai do computador",
    ),
    NetworkFlow(
        "self_check",
        "alfred/diagnostics.py",
        "Autodiagnóstico (alfred --self-check)",
        "127.0.0.1:11434 — este computador",
        "nada: só lista os modelos instalados no Ollama local",
        False,
        "só quando você roda o diagnóstico",
    ),
    NetworkFlow(
        "external_ai",
        "alfred/ai/openai_compat.py",
        "IA externa (compatível com OpenAI)",
        "URL configurada em ai.external_base_url",
        "pedidos, contexto e memórias; imagens só com sua autorização a cada envio",
        True,
        "privacy.network_enabled (desligado por padrão) e escolha do provedor",
    ),
    NetworkFlow(
        "voice_models",
        "alfred/voice/models.py",
        "Download dos modelos de voz (Vosk, Piper)",
        "alphacephei.com, huggingface.co",
        "nada seu: download público com SHA-256 conferido, só quando o modelo falta",
        True,
        "ocorre uma vez, ao ativar a voz pela primeira vez",
    ),
    NetworkFlow(
        "whisper_model",
        "alfred/voice/stt.py",
        "Download do modelo de transcrição (Whisper)",
        "huggingface.co",
        "nada seu: download público, telemetria desligada; depois disso só arquivos locais",
        True,
        "ocorre uma vez, se o modelo não estiver instalado",
    ),
    NetworkFlow(
        "browser",
        "alfred/automation/browser.py",
        "Navegador do ALFRED (perfil dedicado)",
        "os sites que você pedir",
        "o mesmo que um navegador comum enviaria a esses sites",
        True,
        "só abre páginas pedidas; envio de formulários exige confirmação",
    ),
)


@dataclass(frozen=True)
class DataFlow:
    label: str
    state: str
    detail: str
    leaves_computer: bool


def data_flows(settings: Settings, providers: Mapping[str, bool]) -> list[DataFlow]:
    """Estado atual de cada fluxo de dados. ``providers``: nome -> é local?"""
    voice = settings.voice
    privacy = settings.privacy
    ai = settings.ai
    network = privacy.network_enabled

    def ai_state(name: str) -> tuple[str, bool]:
        if name not in providers:
            return f"{name}: indisponível", False
        if providers[name]:
            return f"{name}: local", False
        if not network:
            return f"{name}: externo, bloqueado (rede desligada)", False
        return f"{name}: externo, ativo", True

    ai_label, ai_leaves = ai_state(ai.provider)
    vision_label, _ = ai_state(ai.vision_provider or ai.provider)
    capture = {"ask": "pergunta antes", "allow": "permitida", "deny": "bloqueada"}
    return [
        DataFlow(
            "Microfone",
            "ligado" if voice.enabled else "desligado",
            "O áudio é processado só neste computador (Vosk e Whisper locais); não é gravado "
            "nem enviado.",
            False,
        ),
        DataFlow(
            "Captura de tela",
            capture[privacy.screen_capture],
            "Só sob demanda e com indicação visual; a imagem fica na memória do ALFRED.",
            False,
        ),
        DataFlow("Inteligência artificial", ai_label, "Pedidos, contexto e memórias.", ai_leaves),
        DataFlow(
            "Análise de tela",
            vision_label,
            "Enviar uma imagem a um provedor externo pede sua autorização a cada vez.",
            False,
        ),
        DataFlow(
            "Rede para serviços externos",
            "ligada" if network else "desligada",
            "Controla os provedores externos de IA.",
            network,
        ),
        DataFlow(
            "Histórico e memória",
            "neste computador",
            "SQLite local; consulte, edite ou apague em Histórico e Memória.",
            False,
        ),
        DataFlow(
            "Credenciais",
            "Windows Credential Manager",
            "Chaves nunca vão para arquivos, banco ou registros.",
            False,
        ),
        DataFlow(
            "Downloads de modelos",
            "só quando faltam",
            "Modelos públicos de voz, com hash conferido; nenhum dado seu é enviado.",
            False,
        ),
    ]
