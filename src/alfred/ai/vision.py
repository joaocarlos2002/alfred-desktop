"""Análise de tela por modelos multimodais, sob demanda (RF-008).

A imagem vem da captura sob demanda (que aplica a política ``privacy.screen_capture``), fica só em
memória e vai para um modelo **local** por padrão. Se o provedor de visão configurado for externo,
cada envio exige uma autorização explícita do usuário — nunca é lembrada. Quando a análise é de uma
janela, a árvore UIA dela acompanha a imagem para o modelo localizar elementos pelo nome.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import BaseModel, Field

from alfred.ai.providers import ProviderRegistry
from alfred.core.settings_service import SettingsService
from alfred.core.tools import Risk, Tool, ToolRegistry
from alfred.domain.ai import ChatMessage, LLMProvider, ProviderError, ProviderUnavailableError
from alfred.domain.confirmation import ConfirmationDecision

logger = logging.getLogger(__name__)

MAX_ELEMENTS = 80

VISION_PROMPT = (
    "Você analisa capturas de tela do computador do usuário para o assistente ALFRED. "
    "Responda em português do Brasil, de forma objetiva, apenas com o que é visível na imagem "
    "(e na lista de elementos, quando houver). "
    "Se não for possível responder pela imagem, diga isso."
)

type CaptureFn = Callable[[str, str | None], Awaitable[bytes]]
"""(motivo, janela ou None para o monitor principal) -> PNG."""

type ElementsFn = Callable[[str], Awaitable[list[dict[str, Any]]]]


class Confirmations(Protocol):
    async def request(
        self, *, action: str, target: str, impact: str, category: str
    ) -> ConfirmationDecision: ...


class ExternalUploadDeniedError(PermissionError):
    pass


def is_local(provider: LLMProvider) -> bool:
    """Só provedores que se declaram locais contam como locais; o resto é tratado como externo."""
    return bool(getattr(provider, "local", False))


class ScreenAnalyzer:
    def __init__(
        self,
        registry: ProviderRegistry,
        settings: SettingsService,
        confirmations: Confirmations | None,
        capture: CaptureFn,
        elements: ElementsFn | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._confirmations = confirmations
        self._capture = capture
        self._elements = elements

    async def _model(self, provider: LLMProvider) -> str:
        configured = self._settings.current.ai.vision_model
        if configured:
            return configured
        finder = getattr(provider, "vision_models", None)
        models: list[str] = await finder() if finder is not None else []
        if not models:
            raise ProviderUnavailableError(
                f"Nenhum modelo com visão disponível em {provider.name}. "
                "Instale um modelo multimodal (ex.: llava, gemma3) ou defina ai.vision_model."
            )
        return models[0]

    async def _authorize_upload(self, provider: LLMProvider) -> None:
        if is_local(provider):
            return
        decision = ConfirmationDecision.DENIED
        if self._confirmations is not None:
            decision = await self._confirmations.request(
                action="Enviar uma captura de tela para IA externa",
                target=provider.name,
                impact="A imagem da sua tela sairá deste computador para o provedor externo.",
                category="external_actions",
            )
        if decision is not ConfirmationDecision.APPROVED:
            raise ExternalUploadDeniedError("Envio da captura para IA externa não autorizado")

    async def analyze(
        self, question: str, *, window: str | None = None, include_elements: bool = True
    ) -> dict[str, Any]:
        ai = self._settings.current.ai
        provider = self._registry.get(ai.vision_provider or ai.provider)
        model = await self._model(provider)  # falha cedo: nada é capturado sem modelo
        await self._authorize_upload(provider)
        png = await self._capture(f"análise: {question[:80]}", window)
        text = question
        if window and include_elements and self._elements is not None:
            try:
                found = (await self._elements(window))[:MAX_ELEMENTS]
            except Exception as exc:  # a árvore é complemento; a imagem continua valendo
                logger.warning("Árvore UIA indisponível para '%s': %s", window, exc)
            else:
                text += "\n\nElementos de interface da janela (UIA):\n" + json.dumps(
                    found, ensure_ascii=False
                )
        messages = [
            ChatMessage(role="system", content=VISION_PROMPT),
            ChatMessage(role="user", content=text, images=(png,)),
        ]
        try:
            response = await asyncio.wait_for(
                provider.chat(messages, model=model, timeout=ai.timeout_seconds),
                ai.timeout_seconds + 5,
            )
        except TimeoutError as exc:
            raise ProviderError(f"{provider.name} não analisou a tela a tempo") from exc
        logger.info("Tela analisada por %s/%s", response.provider, response.model)
        return {
            "resposta": response.message.content,
            "provedor": response.provider,
            "modelo": response.model,
            "local": is_local(provider),
        }


class AnalyzeParams(BaseModel):
    pergunta: str = Field(min_length=1, description="O que descobrir na tela")
    janela: str | None = Field(
        default=None,
        description="Janela a analisar (título ou processo); vazio = monitor principal",
    )
    incluir_elementos: bool = Field(
        default=True, description="Anexar a árvore UIA da janela para localizar elementos"
    )


def register_vision_tool(registry: ToolRegistry, analyzer: ScreenAnalyzer) -> None:
    async def analyze(p: AnalyzeParams) -> dict[str, Any]:
        return await analyzer.analyze(
            p.pergunta, window=p.janela, include_elements=p.incluir_elementos
        )

    registry.register(
        Tool(
            "analisar_tela",
            "Captura a tela (ou uma janela) sob demanda e responde a uma pergunta sobre ela "
            "com um modelo multimodal. Use só quando a árvore UIA não bastar.",
            AnalyzeParams,
            analyze,
            "screen",
            Risk.READ,
            timeout=120,
        )
    )
