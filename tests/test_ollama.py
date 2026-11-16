from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from alfred.ai.ollama import OllamaProvider
from alfred.domain.ai import (
    ChatMessage,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ToolCall,
    ToolSpec,
)


def provider(handler: Any) -> OllamaProvider:
    return OllamaProvider(transport=httpx.MockTransport(handler))


async def test_lists_installed_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5"}]})

    assert await provider(handler).list_models() == ["llama3.2:3b", "qwen2.5"]


async def test_chat_sends_tools_and_parses_tool_calls_and_usage() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "llama3.2:3b",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "abrir_aplicativo", "arguments": {"nome": "calc"}}}
                    ],
                },
                "prompt_eval_count": 42,
                "eval_count": 7,
            },
        )

    tool = ToolSpec(name="abrir_aplicativo", description="Abre", parameters={"type": "object"})
    history = [
        ChatMessage(role="user", content="abra a calculadora"),
        ChatMessage(role="assistant", tool_calls=(ToolCall(id="0", name="x", arguments={"a": 1}),)),
        ChatMessage(role="tool", content="ok", tool_call_id="0"),
    ]
    response = await provider(handler).chat(history, model="llama3.2:3b", tools=[tool])

    assert seen["stream"] is False
    assert seen["keep_alive"] == "5m"
    assert seen["tools"][0]["function"]["name"] == "abrir_aplicativo"
    assert seen["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"a": 1}
    assert response.tool_calls[0].name == "abrir_aplicativo"
    assert response.tool_calls[0].arguments == {"nome": "calc"}
    assert (response.usage.input_tokens, response.usage.output_tokens) == (42, 7)


async def test_string_arguments_are_decoded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        call = {"function": {"name": "t", "arguments": '{"x": 2}'}}
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": [call]}})

    response = await provider(handler).chat([], model="m")
    assert response.tool_calls[0].arguments == {"x": 2}


async def test_offline_and_timeout_are_transient_errors() -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusada", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    with pytest.raises(ProviderUnavailableError, match="instalado"):
        await provider(offline).list_models()
    with pytest.raises(ProviderTimeoutError):
        await provider(slow).chat([], model="m", timeout=1)


@pytest.mark.windows
async def test_real_ollama_if_running() -> None:
    real = OllamaProvider()
    try:
        models = await real.list_models()
    except ProviderUnavailableError:
        pytest.skip("Ollama não está instalado/em execução nesta máquina")
    if not models:
        pytest.skip("Ollama sem modelos baixados")
    response = await real.chat(
        [ChatMessage(role="user", content="Responda apenas: ok")], model=models[0], timeout=120
    )
    assert response.message.content.strip()
