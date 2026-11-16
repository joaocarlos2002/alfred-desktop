"""E2E em desktop Windows real: o orquestrador coordena duas aplicações via UI Automation.

Usa a aplicação de teste ``tests/fixtures/uia_app.py`` (sem dados do usuário). Só UIA: nenhuma tecla
simulada nem clique por coordenada, então o teclado e o mouse do usuário não são usados.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from alfred.ai.fake import FakeProvider
from alfred.core.event_bus import EventBus
from alfred.core.orchestrator import Orchestrator
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import ToolRegistry
from alfred.domain.ai import ChatMessage, ToolCall

pytestmark = [
    pytest.mark.windows,
    pytest.mark.skipif(sys.platform != "win32", reason="UI Automation do Windows"),
]

APP = Path(__file__).resolve().parents[1] / "fixtures" / "uia_app.py"


class MemoryActivity:
    async def add(self, entry: Any) -> Any:
        return entry


class Allow:
    async def authorize(self, *args: object, **kwargs: object) -> bool:
        return True


class Planner:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    async def chat(self, messages: list[ChatMessage], *, tools: Any = None) -> Any:
        return await self.provider.chat(messages, model="fake", tools=tools)


def _wait_window(title: str, timeout: float = 15.0) -> None:
    from alfred.automation.windows import list_windows

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(title in w.title for w in list_windows()):
            return
        time.sleep(0.2)
    raise AssertionError(f"janela '{title}' não apareceu")


@pytest.fixture
def two_apps() -> Iterator[tuple[str, str]]:
    tag = uuid.uuid4().hex[:6]
    source, target = f"ALFRED Teste Origem {tag}", f"ALFRED Teste Destino {tag}"
    # O conftest põe o Qt em modo "offscreen"; as aplicações de teste precisam de janelas reais.
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    procs = [
        subprocess.Popen([sys.executable, str(APP), source, f"pedido {tag}"], env=env),
        subprocess.Popen([sys.executable, str(APP), target, ""], env=env),
    ]
    try:
        _wait_window(source)
        _wait_window(target)
        yield source, target
    finally:
        for proc in procs:  # só os processos que este teste abriu
            proc.terminate()
            proc.wait(timeout=10)


def _last_tool_result(messages: list[ChatMessage]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(
        next(m for m in reversed(messages) if m.role == "tool").content
    )
    return result


async def test_orchestrator_moves_data_between_two_real_applications(
    two_apps: tuple[str, str],
) -> None:
    from alfred.automation.tools import register_automation_tools
    from alfred.automation.uia_service import UiaService
    from alfred.automation.window_service import WindowService
    from alfred.core.action_log import ActionLog

    source, target = two_apps
    log = ActionLog(MemoryActivity())  # type: ignore[arg-type]
    tools = ToolRegistry()
    register_automation_tools(
        tools,
        apps=None,  # type: ignore[arg-type]
        windows=WindowService(log),
        uia=UiaService(log),
        keyboard=None,  # type: ignore[arg-type]
        browser=None,  # type: ignore[arg-type]
    )
    tools.allow_only({"ler_elemento", "preencher_elemento", "clicar_elemento"})

    def copy_step(messages: list[ChatMessage], _: Any) -> ChatMessage:
        text = _last_tool_result(messages)["texto"]  # o plano usa o resultado real da leitura
        return ChatMessage(
            role="assistant",
            tool_calls=(
                ToolCall(
                    id="2",
                    name="preencher_elemento",
                    arguments={"janela": target, "nome_elemento": "Entrada", "texto": text},
                ),
                ToolCall(
                    id="3",
                    name="clicar_elemento",
                    arguments={"janela": target, "nome_elemento": "Confirmar"},
                ),
                ToolCall(
                    id="4",
                    name="ler_elemento",
                    arguments={"janela": target, "nome_elemento": "Resultado"},
                ),
            ),
        )

    def final(messages: list[ChatMessage], _: Any) -> ChatMessage:
        return ChatMessage(role="assistant", content=_last_tool_result(messages)["texto"])

    provider = FakeProvider(
        replies=[
            ChatMessage(
                role="assistant",
                tool_calls=(
                    ToolCall(
                        id="1",
                        name="ler_elemento",
                        arguments={"janela": source, "nome_elemento": "Origem"},
                    ),
                ),
            ),
            copy_step,
            final,
        ]
    )
    bus = EventBus()
    await bus.start()
    try:
        orchestrator = Orchestrator(
            Planner(provider),  # type: ignore[arg-type]
            tools,
            Allow(),  # type: ignore[arg-type]
            TaskRegistry(),
            bus,
            speak=False,
        )
        reply = await asyncio.wait_for(
            orchestrator.run("copie o pedido da origem para o destino e confirme"), 90
        )
    finally:
        await bus.stop()
    tag = source.rsplit(" ", 1)[-1]
    assert reply == f"recebido: pedido {tag}"  # lido de volta da aplicação de destino
