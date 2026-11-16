"""Contratos de provedores de IA (RF-009). Nenhum tipo de SDK específico vaza daqui."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    """Para ``role="tool"``: a qual chamada esta mensagem responde."""


class ToolSpec(BaseModel):
    """Ferramenta oferecida ao modelo (o modelo só propõe; quem executa é o orquestrador)."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]
    """JSON Schema dos argumentos."""


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    message: ChatMessage
    provider: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    latency_seconds: float = 0.0

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return self.message.tool_calls


class ProviderError(Exception):
    """Falha de um provedor de IA."""

    transient = False


class ProviderUnavailableError(ProviderError):
    """Serviço fora do ar, sem rede ou recusado (pode valer nova tentativa)."""

    transient = True


class ProviderTimeoutError(ProviderError):
    transient = True


class ProviderAuthError(ProviderError):
    """Credencial ausente ou inválida."""


class LLMProvider(Protocol):
    name: str

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        timeout: float = 60.0,
    ) -> ChatResponse: ...

    async def list_models(self) -> list[str]: ...
