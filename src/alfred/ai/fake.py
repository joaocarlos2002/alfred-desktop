"""Provedor roteirizado para testes do orquestrador, sem rede nem modelo."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from alfred.domain.ai import ChatMessage, ChatResponse, ProviderError, ToolSpec, Usage

type Script = Callable[[list[ChatMessage], list[ToolSpec] | None], ChatMessage | ProviderError]


class FakeProvider:
    def __init__(self, name: str = "fake", replies: list[Any] | None = None) -> None:
        """``replies``: sequência de ``ChatMessage`` (ou exceções a lançar), ou um callable."""
        self.name = name
        self._replies = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        timeout: float = 60.0,
    ) -> ChatResponse:
        self.calls.append({"messages": list(messages), "model": model, "tools": tools})
        if not self._replies:
            raise ProviderError("FakeProvider sem respostas roteirizadas")
        reply = self._replies.pop(0)
        if callable(reply) and not isinstance(reply, ChatMessage):
            reply = reply(messages, tools)
        if isinstance(reply, BaseException):
            raise reply
        return ChatResponse(
            message=reply,
            provider=self.name,
            model=model,
            usage=Usage(
                input_tokens=sum(len(m.content) for m in messages), output_tokens=len(reply.content)
            ),
        )

    async def list_models(self) -> list[str]:
        return ["fake-model"]
