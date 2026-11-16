"""Fluxos principais com a aplicação real montada pelo ``bootstrap`` (Seção 10).

Só o modelo de IA é substituído (``FakeProvider``); event bus, banco, orquestrador, permissões,
confirmações, histórico, memória, gatilhos e interrupção de emergência são os de produção.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from alfred.ai.fake import FakeProvider
from alfred.app import Application, bootstrap
from alfred.core.tools import Risk, Tool
from alfred.domain.ai import ChatMessage, ProviderUnavailableError, ToolCall
from alfred.domain.confirmation import ConfirmationDecision, ConfirmationRequested
from alfred.domain.events import Event
from alfred.domain.tasks import (
    StepStatus,
    TaskFinished,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)
from alfred.domain.triggers import TriggerSpec
from alfred.domain.voice import SpeakRequested, UtteranceTranscribed
from alfred.infrastructure.db.task_repository import SqlTaskRepository


class NoteParams(BaseModel):
    texto: str


class Desktop:
    """Efeitos observáveis das ferramentas de teste."""

    def __init__(self) -> None:
        self.notes: list[str] = []
        self.sent: list[str] = []
        self.release = asyncio.Event()


def call(name: str, call_id: str, **args: Any) -> ChatMessage:
    return ChatMessage(
        role="assistant", tool_calls=(ToolCall(id=call_id, name=name, arguments=args),)
    )


def say(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)


async def started_app(tmp_path: Path, replies: list[Any], desktop: Desktop) -> Application:
    provider = FakeProvider("fake", replies=replies)
    app, _ = bootstrap(tmp_path, ai_providers=(provider,))
    assert app.tools is not None
    assert app.settings is not None

    async def note(p: NoteParams) -> dict[str, Any]:
        desktop.notes.append(p.texto)
        return {"anotado": p.texto}

    async def send(p: NoteParams) -> dict[str, Any]:
        desktop.sent.append(p.texto)
        return {"enviado": True}

    async def slow(p: NoteParams) -> dict[str, Any]:
        await desktop.release.wait()
        desktop.notes.append(p.texto)
        return {}

    app.tools.register(Tool("anotar", "Anota um texto", NoteParams, note, "notes", Risk.READ))
    app.tools.register(
        Tool("enviar_mensagem", "Envia", NoteParams, send, "messages", Risk.SENSITIVE)
    )
    app.tools.register(Tool("tarefa_longa", "Demora", NoteParams, slow, "notes"))
    await app.manager.start_all()
    await app.settings.update("ai", provider="fake")
    return app


@pytest.fixture
def desktop() -> Desktop:
    return Desktop()


def record_all(app: Application) -> list[Event]:
    seen: list[Event] = []

    async def keep(event: Event) -> None:
        seen.append(event)

    app.bus.subscribe(Event, keep)
    return seen


async def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condição não atingida a tempo")
        await asyncio.sleep(0.02)


def utterance(text: str) -> UtteranceTranscribed:
    return UtteranceTranscribed(
        text=text, audio_seconds=1, latency_seconds=0.1, trigger="wake_word"
    )


async def test_voice_request_runs_through_orchestrator_with_real_history_and_memory(
    tmp_path: Path, desktop: Desktop
) -> None:
    app = await started_app(
        tmp_path, [call("anotar", "1", texto="comprar pão"), say("Anotei: comprar pão.")], desktop
    )
    seen = record_all(app)
    try:
        await app.bus.publish(utterance("Alfred, anote comprar pão"))
        await wait_for(lambda: any(isinstance(e, TaskFinished) for e in seen))
        await app.bus.drain(timeout=2)
        assert desktop.notes == ["comprar pão"]  # efeito real, não só a resposta
        spoken = [e.text for e in seen if isinstance(e, SpeakRequested)]
        assert spoken == ["Anotei: comprar pão."]  # ALFRED responde por voz
        assert app.database is not None
        [task] = await SqlTaskRepository(app.database).list_tasks()
        assert task.status is TaskStatus.SUCCEEDED
        assert [s.status for s in task.steps] == [StepStatus.SUCCEEDED]
        assert task.steps[0].result == {"anotado": "comprar pão"}  # resultado real no histórico
        conversations = await app.memory._store.conversations()
        assert conversations[0][2] == 2  # pedido e resposta registrados localmente
    finally:
        await app.manager.stop_all()


async def test_sensitive_operation_waits_for_confirmation(tmp_path: Path, desktop: Desktop) -> None:
    app = await started_app(
        tmp_path,
        [call("enviar_mensagem", "1", texto="oi"), say("Não enviei: você recusou.")],
        desktop,
    )
    seen = record_all(app)
    assert app.orchestrator is not None
    assert app.confirmations is not None
    try:
        task = app.orchestrator.submit("mande oi para a equipe", origin="ui")
        await wait_for(lambda: any(isinstance(e, ConfirmationRequested) for e in seen))
        request = next(e for e in seen if isinstance(e, ConfirmationRequested))
        assert desktop.sent == []  # nada é enviado antes da decisão
        app.confirmations.resolve(request.request_id, ConfirmationDecision.DENIED)
        assert await task == "Não enviei: você recusou."
        await app.bus.drain(timeout=2)
        assert desktop.sent == []
        steps = [e.status for e in seen if isinstance(e, TaskStepFinished)]
        assert steps == [StepStatus.DENIED]
    finally:
        await app.manager.stop_all()


async def test_emergency_stop_cancels_running_task(tmp_path: Path, desktop: Desktop) -> None:
    app = await started_app(tmp_path, [call("tarefa_longa", "1", texto="x")], desktop)
    seen = record_all(app)
    assert app.orchestrator is not None
    assert app.emergency is not None
    try:
        task = app.orchestrator.submit("faça a tarefa longa", origin="ui")
        await wait_for(lambda: any(isinstance(e, TaskStepStarted) for e in seen))
        stopped = await app.emergency.trigger("teste")
        assert stopped.cancelled_tasks >= 1
        with pytest.raises(asyncio.CancelledError):
            await task
        desktop.release.set()
        await app.bus.drain(timeout=2)
        assert desktop.notes == []  # a etapa não chegou a produzir efeito
        finished = next(e for e in seen if isinstance(e, TaskFinished))
        assert finished.status is TaskStatus.CANCELLED
    finally:
        await app.manager.stop_all()


async def test_trigger_created_by_user_runs_through_orchestrator(
    tmp_path: Path, desktop: Desktop
) -> None:
    app = await started_app(
        tmp_path, [call("anotar", "1", texto="ponto batido"), say("Ponto registrado.")], desktop
    )
    seen = record_all(app)
    try:
        trigger = await app.triggers.save(
            TriggerSpec(
                name="Bater ponto",
                kind="voice",
                config={"frase": "bater ponto"},
                request="anote ponto batido",
            )
        )
        await app.bus.publish(utterance("Alfred, bater ponto"))
        await wait_for(lambda: desktop.notes == ["ponto batido"])
        await wait_for(
            lambda: any(
                isinstance(e, TaskFinished) and e.status is TaskStatus.SUCCEEDED for e in seen
            )
        )
        await app.bus.drain(timeout=2)
        records = await app.triggers.executions(trigger.id)
        assert [r.status for r in records] == ["succeeded"]
    finally:
        await app.manager.stop_all()


async def test_essential_functions_work_without_remote_services(tmp_path: Path) -> None:
    """Sem rede e sem IA disponível: comandos locais seguem funcionando e a falha é honesta."""
    app, _ = bootstrap(tmp_path)  # nenhum provedor de IA registrado (ex.: Ollama ausente)
    seen = record_all(app)
    await app.manager.start_all()
    assert app.settings is not None
    assert app.settings.current.privacy.network_enabled is False  # padrão: rede desligada
    assert app.orchestrator is not None
    assert app.ai is not None
    try:
        await app.bus.publish(utterance("Alfred, que horas são?"))
        await wait_for(lambda: any(isinstance(e, SpeakRequested) for e in seen))
        spoken = next(e for e in seen if isinstance(e, SpeakRequested)).text
        assert "hora" in spoken.lower()
        reply = await app.orchestrator.run("resuma meus e-mails", origin="ui")
        assert "inteligência artificial" in reply  # explica a indisponibilidade, sem inventar
        with pytest.raises(ProviderUnavailableError):
            app.ai.registry.get("ollama")
    finally:
        await app.manager.stop_all()
