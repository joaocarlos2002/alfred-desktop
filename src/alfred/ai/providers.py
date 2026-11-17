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
    ModelLoading,
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
        self._warming: asyncio.Future[None] | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    async def start(self) -> None:
        # Sobe o servidor local em segundo plano: o primeiro pedido não espera por isso.
        self._boot = asyncio.ensure_future(self._ensure_local_server())

    async def stop(self) -> None:
        boot = getattr(self, "_boot", None)
        if boot is not None and not boot.done():
            boot.cancel()

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
        try:
            model = ai.model or await self._default_model(provider)
        except ProviderUnavailableError:
            # Ollama instalado mas parado (ex.: após reiniciar o PC): inicia e tenta de novo.
            if not await self._ensure_local_server():
                raise
            model = ai.model or await self._default_model(provider)
        timeout = ai.timeout_seconds
        if await self._needs_loading(provider, model):
            # Sem isso, o primeiro pedido estourava o limite enquanto o modelo ainda carregava
            # e a nova tentativa recomeçava a espera do zero.
            timeout += ai.load_timeout_seconds
            logger.info("Modelo %s/%s não está carregado; aguardando a carga", provider.name, model)
            await self._bus.publish(ModelLoading(provider=provider.name, model=model))
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    provider.chat(messages, model=model, tools=tools, timeout=timeout),
                    timeout + 5,
                )
            except TimeoutError as exc:
                error: ProviderError = ProviderTimeoutError(
                    f"{provider.name} não respondeu em {timeout} s"
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

    async def _ensure_local_server(self) -> bool:
        ai = self.config
        if not ai.start_local_server:
            return False
        try:
            provider = self.registry.get(ai.provider)
        except ProviderError:
            return False
        ensure = getattr(provider, "ensure_running", None)
        if ensure is None or not getattr(provider, "local", False):
            return False
        started: bool = await ensure()
        if started:
            logger.info("Servidor de IA local (%s) disponível", provider.name)
        return started

    @staticmethod
    async def _needs_loading(provider: LLMProvider, model: str) -> bool:
        check = getattr(provider, "is_loaded", None)
        if check is None:
            return False
        try:
            return not await check(model)
        except ProviderError:
            return False  # o próprio pedido vai reportar a falha

    async def warm_up(self) -> None:
        """Pré-carrega o modelo local enquanto o usuário ainda fala (#47).

        Carregar um modelo de ~5 GB leva ~20 s na primeira vez: começar ao detectar a palavra
        de ativação esconde boa parte disso. Só para provedores locais com ``warm_up`` e
        sem erro visível: se falhar, o pedido real reporta o problema."""
        ai = self.config
        try:
            provider = self.registry.get(ai.provider)
            warm = getattr(provider, "warm_up", None)
            if warm is None or not getattr(provider, "local", False):
                return
            if self._warming is not None and not self._warming.done():
                return
            model = ai.model or await self._default_model(provider)
            self._warming = asyncio.ensure_future(warm(model))
            await self._warming
        except ProviderError as exc:
            logger.debug("Pré-carga da IA ignorada: %s", exc)

    async def _default_model(self, provider: LLMProvider) -> str:
        models = await provider.list_models()
        if not models:
            raise ProviderUnavailableError(f"Nenhum modelo disponível em {provider.name}")
        return models[0]
