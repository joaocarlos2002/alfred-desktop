from __future__ import annotations

import json
import logging
import sys
import uuid
from typing import Any

import httpx
import pytest

from alfred.ai.openai_compat import OpenAICompatibleProvider
from alfred.domain.ai import ChatMessage, ProviderAuthError, ProviderUnavailableError, ToolSpec
from alfred.infrastructure.logging_setup import SecretRedactor

KEY = "sk-teste-0123456789abcdefghij"


class MemoryCredentials:
    def __init__(self, secret: str | None = KEY) -> None:
        self.secret = secret

    def get(self, name: str) -> str | None:
        return self.secret


def provider(
    handler: Any, *, network: bool = True, secret: str | None = KEY
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "externo",
        base_url=lambda: "https://api.exemplo.com/v1",
        credentials=MemoryCredentials(secret),
        network_enabled=lambda: network,
        transport=httpx.MockTransport(handler),
    )


def never(request: httpx.Request) -> httpx.Response:
    raise AssertionError("nenhuma requisição deveria ter sido feita")


async def test_network_switch_off_makes_no_request_at_all() -> None:
    with pytest.raises(ProviderUnavailableError, match="rede desligado"):
        await provider(never, network=False).chat([], model="m")
    with pytest.raises(ProviderUnavailableError):
        await provider(never, network=False).list_models()


async def test_missing_or_rejected_credential() -> None:
    with pytest.raises(ProviderAuthError, match="Credential Manager"):
        await provider(never, secret=None).chat([], model="m")
    with pytest.raises(ProviderAuthError, match="401"):
        await provider(lambda r: httpx.Response(401)).chat([], model="m")


async def test_chat_with_tools_and_bearer_auth() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-x",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "abrir_aplicativo",
                                        "arguments": '{"nome": "calc"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5},
            },
        )

    tool = ToolSpec(name="abrir_aplicativo", description="Abre", parameters={"type": "object"})
    response = await provider(handler).chat(
        [ChatMessage(role="user", content="abra a calculadora")], model="gpt-x", tools=[tool]
    )
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["url"] == "https://api.exemplo.com/v1/chat/completions"
    assert seen["body"]["tools"][0]["function"]["name"] == "abrir_aplicativo"
    assert response.tool_calls[0].arguments == {"nome": "calc"}
    assert response.usage.input_tokens == 30


async def test_rate_limit_is_transient() -> None:
    with pytest.raises(ProviderUnavailableError, match="429"):
        await provider(lambda r: httpx.Response(429)).chat([], model="m")


def test_registered_secret_is_masked_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    redactor = SecretRedactor()
    redactor.register("chave-qualquer-xyz")
    assert "chave-qualquer-xyz" not in redactor.redact("enviando chave-qualquer-xyz para a API")


@pytest.mark.skipif(sys.platform != "win32", reason="Credential Manager")
@pytest.mark.windows
def test_real_credential_manager_roundtrip(caplog: pytest.LogCaptureFixture) -> None:
    from alfred.infrastructure.credentials import CredentialStore

    store = CredentialStore(
        service=f"ALFRED-test-{uuid.uuid4().hex[:8]}", secret_redactor=SecretRedactor()
    )
    secret = "sk-real-test-" + uuid.uuid4().hex
    with caplog.at_level(logging.DEBUG):
        store.set("externo", secret)
        assert store.get("externo") == secret
        store.delete("externo")
        assert store.get("externo") is None
        store.delete("externo")  # idempotente
    assert secret not in caplog.text
