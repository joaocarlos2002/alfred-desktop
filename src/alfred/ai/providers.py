"""Registro e seleção de provedores de IA, com timeout, novas tentativas e métricas (RF-009)."""

from __future__ import annotations

import asyncio
import logging
import time

from alfred.config.settings import AISettings
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.ai import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ToolSpec,
)
from alfred.domain.module import Health, HealthStatus, Module

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def register(self, provider: LLMProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderUnavailableError(f"Provedor de IA '{name}' não está disponível") from exc

    @property
    def names(self) -> list[str]:
        return sorted(self._providers)


class AIService(Module):
    """Ponto único de acesso a modelos: o orquestrador nunca fala com um provedor diretamente."""

    name = "ai"
    depends_on = ("settings", "event_bus")
    optional = True

    def __init__(
        self,
        registry: ProviderRegistry,
        settings: SettingsService,
        bus: EventBus,
        *,
        backoff: float = 0.5,
    ) -> None:
        self.registry = registry
        self._settings = settings
        self._bus = bus
        self._backoff = backoff
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def health(self) -> Health:
        ai = self.config
        if ai.provider not in self.registry.names:
            return Health(status=HealthStatus.DEGRADED, detail=f"provedor '{ai.provider}' ausente")
        return Health(status=HealthStatus.HEALTHY, detail=f"{ai.provider}/{ai.model or 'padrão'}")

    @property
    def config(self) -> AISettings:
        return self._settings.current.ai

    async def chat(
        self, messages: list[ChatMessage], *, tools: list[ToolSpec] | None = None
    ) -> ChatResponse:
        ai = self.config
        provider = self.registry.get(ai.provider)
        model = ai.model or await self._default_model(provider)
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    provider.chat(messages, model=model, tools=tools, timeout=ai.timeout_seconds),
                    ai.timeout_seconds + 5,
                )
            except TimeoutError as exc:
                error: ProviderError = ProviderTimeoutError(
                    f"{provider.name} não respondeu em {ai.timeout_seconds} s"
                )
                error.__cause__ = exc
            except ProviderError as exc:
                error = exc
            else:
                self.total_input_tokens += response.usage.input_tokens
                self.total_output_tokens += response.usage.output_tokens
                logger.info(
                    "IA %s/%s: %d→%d tokens em %.2f s",
                    response.provider,
                    response.model,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    time.perf_counter() - started,
                )
                return response
            if not error.transient or attempt >= ai.max_retries:
                raise error
            attempt += 1
            delay = self._backoff * 2 ** (attempt - 1)
            logger.warning("IA falhou (%s); nova tentativa %d em %.1f s", error, attempt, delay)
            await asyncio.sleep(delay)

    async def _default_model(self, provider: LLMProvider) -> str:
        models = await provider.list_models()
        if not models:
            raise ProviderUnavailableError(f"Nenhum modelo disponível em {provider.name}")
        return models[0]
