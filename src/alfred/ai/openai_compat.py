"""Provedor externo compatível com a API da OpenAI (RF-009).

Cobre OpenAI, OpenRouter, Groq, LM Studio etc. pela mesma interface. Respeita o interruptor
``privacy.network_enabled``: com a rede desligada, **nenhuma requisição é feita**. A chave vem do
Windows Credential Manager e nunca é registrada em logs.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from alfred.domain.ai import (
    ChatMessage,
    ChatResponse,
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ToolCall,
    ToolSpec,
    Usage,
)


class SecretSource(Protocol):
    def get(self, name: str) -> str | None: ...


def _message(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in message.tool_calls
        ]
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    return payload


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        *,
        base_url: Callable[[], str],
        credentials: SecretSource,
        network_enabled: Callable[[], bool],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url
        self._credentials = credentials
        self._network_enabled = network_enabled
        self._transport = transport

    async def _request(self, method: str, path: str, timeout: float, **kwargs: Any) -> Any:
        if not self._network_enabled():
            raise ProviderUnavailableError(
                "Acesso à rede desligado (Privacidade → Permitir acesso à rede)"
            )
        key = self._credentials.get(self.name)
        if not key:
            raise ProviderAuthError(f"Sem chave para '{self.name}' no Credential Manager")
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url().rstrip("/"), timeout=timeout, transport=self._transport
            ) as client:
                response = await client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"{self.name} não respondeu em {timeout:.0f} s") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                f"{self.name} indisponível ({type(exc).__name__})"
            ) from exc
        if response.status_code in (401, 403):
            raise ProviderAuthError(f"{self.name} recusou a credencial ({response.status_code})")
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailableError(f"{self.name} respondeu {response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.name} respondeu {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    async def list_models(self) -> list[str]:
        data = await self._request("GET", "/models", 15)
        return sorted(item["id"] for item in data.get("data", []))

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        timeout: float = 60.0,
    ) -> ChatResponse:
        body: dict[str, Any] = {"model": model, "messages": [_message(m) for m in messages]}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        started = time.perf_counter()
        data = await self._request("POST", "/chat/completions", timeout, json=body)
        raw = data["choices"][0]["message"]
        calls = tuple(
            ToolCall(
                id=c.get("id") or uuid.uuid4().hex,
                name=c["function"]["name"],
                arguments=json.loads(c["function"].get("arguments") or "{}"),
            )
            for c in raw.get("tool_calls") or []
        )
        usage = data.get("usage") or {}
        return ChatResponse(
            message=ChatMessage(
                role="assistant", content=raw.get("content") or "", tool_calls=calls
            ),
            provider=self.name,
            model=data.get("model", model),
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
            latency_seconds=round(time.perf_counter() - started, 3),
        )
