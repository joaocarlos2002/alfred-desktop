from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, Field

from alfred.core.tools import (
    DuplicateCallError,
    Risk,
    Tool,
    ToolError,
    ToolNotAllowedError,
    ToolRegistry,
    ToolValidationError,
)
from alfred.domain.ai import ToolCall


class OpenParams(BaseModel):
    nome: str = Field(min_length=1)


class SlowParams(BaseModel):
    pass


def registry() -> tuple[ToolRegistry, list[str]]:
    ran: list[str] = []

    async def open_app(p: OpenParams) -> dict[str, Any]:
        ran.append(p.nome)
        return {"app": p.nome}

    async def read(p: OpenParams) -> dict[str, Any]:
        return {"texto": p.nome}

    async def slow(p: SlowParams) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    reg = ToolRegistry()
    reg.register(Tool("abrir_aplicativo", "Abre", OpenParams, open_app, "applications"))
    reg.register(Tool("ler", "Lê", OpenParams, read, "ui", risk=Risk.READ, idempotent=True))
    reg.register(Tool("lenta", "Demora", SlowParams, slow, "test", timeout=0.05))
    return reg, ran


def call(name: str, call_id: str = "1", **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def test_specs_expose_json_schema_for_tool_calling() -> None:
    reg, _ = registry()
    spec = next(s for s in reg.specs() if s.name == "abrir_aplicativo")
    assert spec.parameters["properties"]["nome"]["type"] == "string"
    assert spec.parameters["required"] == ["nome"]


async def test_valid_call_executes() -> None:
    reg, ran = registry()
    assert await reg.execute(call("abrir_aplicativo", nome="Calculadora")) == {"app": "Calculadora"}
    assert ran == ["Calculadora"]


async def test_invalid_arguments_are_rejected_before_running() -> None:
    reg, ran = registry()
    with pytest.raises(ToolValidationError, match="nome"):
        await reg.execute(call("abrir_aplicativo", nome=""))
    with pytest.raises(ToolValidationError):
        await reg.execute(call("abrir_aplicativo", outro="x"))
    assert ran == []


async def test_allowlist_blocks_everything_else() -> None:
    reg, ran = registry()
    reg.allow_only({"ler"})
    assert [s.name for s in reg.specs()] == ["ler"]
    with pytest.raises(ToolNotAllowedError):
        await reg.execute(call("abrir_aplicativo", nome="x"))
    with pytest.raises(ToolNotAllowedError):
        await reg.execute(call("formatar_disco"))
    assert ran == []
    with pytest.raises(ValueError, match="desconhecidas"):
        reg.allow_only({"inexistente"})


async def test_duplicate_call_is_not_executed_twice_unless_idempotent() -> None:
    reg, ran = registry()
    await reg.execute(call("abrir_aplicativo", "c7", nome="x"))
    with pytest.raises(DuplicateCallError):
        await reg.execute(call("abrir_aplicativo", "c7", nome="x"))
    assert ran == ["x"]
    await reg.execute(call("ler", "c8", nome="a"))
    await reg.execute(call("ler", "c8", nome="a"))  # leitura pode repetir


async def test_timeout() -> None:
    reg, _ = registry()
    with pytest.raises(ToolError, match="excedeu"):
        await reg.execute(call("lenta"))


def test_real_automation_tools_are_registered(tmp_path: Any) -> None:
    from alfred.app import bootstrap

    app, _ = bootstrap(tmp_path, app_lister=lambda: [])
    assert app.tools is not None
    names = {s.name for s in app.tools.specs()}
    assert {"abrir_aplicativo", "clicar_elemento", "digitar_texto", "abrir_pagina"} <= names
