"""Provedor local Ollama (RF-009) — funciona totalmente offline com um modelo local.

Fala com a API HTTP do Ollama (padrão ``http://127.0.0.1:11434``). ``keep_alive`` controla por
quanto tempo o modelo fica carregado após o uso (Diretriz 3: nada pesado permanente em memória).
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

from alfred.domain.ai import (
    ChatMessage,
    ChatResponse,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ToolCall,
    ToolSpec,
    Usage,
)

DEFAULT_URL = "http://127.0.0.1:11434"


def _to_ollama(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.images:
        payload["images"] = [base64.b64encode(png).decode("ascii") for png in message.images]
    if message.tool_calls:
        payload["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}} for c in message.tool_calls
        ]
    return payload


def _tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _tool_calls(raw: list[dict[str, Any]] | None) -> tuple[ToolCall, ...]:
    calls = []
    for item in raw or []:
        function = item.get("function", {})
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        calls.append(
            ToolCall(
                id=item.get("id") or uuid.uuid4().hex, name=function["name"], arguments=arguments
            )
        )
    return tuple(calls)


class OllamaProvider:
    name = "ollama"
    local = True

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        *,
        keep_alive: str = "5m",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        import httpx  # sob demanda: ~0,2 s a menos na inicialização (#47)

        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport)

    async def _request(self, method: str, path: str, timeout: float, **kwargs: Any) -> Any:
        import httpx

        try:
            async with self._client(timeout) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Ollama não respondeu em {timeout:.0f} s") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                f"Ollama indisponível em {self.base_url} (está instalado e em execução?)"
            ) from exc
        if response.status_code == 404:
            detail = response.json().get("error", "") if response.content else ""
            raise ProviderError(f"Ollama: {detail or 'recurso não encontrado'}")
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"Ollama respondeu {response.status_code}")
        response.raise_for_status()
        return response.json()

    async def list_models(self) -> list[str]:
        data = await self._request("GET", "/api/tags", 10)
        return [model["name"] for model in data.get("models", [])]

    async def warm_up(self, model: str) -> None:
        """Carrega o modelo na memória (GPU/CPU) sem gerar texto; some após ``keep_alive``."""
        await self._request(
            "POST", "/api/generate", 120, json={"model": model, "keep_alive": self.keep_alive}
        )

    async def vision_models(self) -> list[str]:
        """Modelos instalados que aceitam imagens (capacidade "vision" do Ollama)."""
        found = []
        for model in await self.list_models():
            info = await self._request("POST", "/api/show", 10, json={"model": model})
            if "vision" in (info.get("capabilities") or []):
                found.append(model)
        return found

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        timeout: float = 60.0,
    ) -> ChatResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [_to_ollama(m) for m in messages],
            "stream": False,
            "keep_alive": self.keep_alive,
        }
        if tools:
            body["tools"] = [_tool(t) for t in tools]
        started = time.perf_counter()
        data = await self._request("POST", "/api/chat", timeout, json=body)
        raw = data.get("message", {})
        message = ChatMessage(
            role="assistant",
            content=raw.get("content", "") or "",
            tool_calls=_tool_calls(raw.get("tool_calls")),
        )
        return ChatResponse(
            message=message,
            provider=self.name,
            model=data.get("model", model),
            usage=Usage(
                input_tokens=int(data.get("prompt_eval_count", 0) or 0),
                output_tokens=int(data.get("eval_count", 0) or 0),
            ),
            latency_seconds=round(time.perf_counter() - started, 3),
        )
