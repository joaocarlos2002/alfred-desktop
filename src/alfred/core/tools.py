"""Catálogo de ferramentas que a IA pode **propor** (ADR-0002, seção 6).

A IA nunca executa nada: ela devolve chamadas de ferramenta; o orquestrador valida aqui (allowlist +
schema Pydantic) e só então executa, com timeout e proteção contra execução duplicada.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from alfred.domain.ai import ToolCall, ToolSpec

logger = logging.getLogger(__name__)


class Risk(StrEnum):
    READ = "read"
    """Só lê informações (ex.: ler texto de um elemento)."""
    ACTION = "action"
    """Altera o estado do computador de forma reversível (abrir app, digitar)."""
    SENSITIVE = "sensitive"
    """Pode ser irreversível ou externo: sujeito a confirmação (seção 6)."""


class ToolError(Exception):
    """Erro devolvido ao planejador como resultado da ferramenta (não derruba a tarefa)."""


class ToolNotAllowedError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


class DuplicateCallError(ToolError):
    pass


type Handler = Callable[[Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Handler
    category: str
    risk: Risk = Risk.ACTION
    timeout: float = 30.0
    idempotent: bool = False
    resource: str | None = None
    """Recurso compartilhado ("input", "browser"...): etapas do mesmo recurso são serializadas."""
    untrusted_output: bool = False
    """Devolve conteúdo de terceiros (página, janela, tela) que pode conter prompt injection."""
    needs_confirmation: Callable[[Any], str | None] | None = None
    """Recebe os parâmetros validados; devolve o motivo quando *esta* chamada exige confirmação
    mesmo com a política "permitir" (ex.: abrir um interpretador de comandos)."""

    def spec(self) -> ToolSpec:
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, parameters=schema)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._allowed: set[str] | None = None  # None = todas as registradas
        self._executed: set[str] = set()

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Ferramenta duplicada: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove a ferramenta (ex.: skill desligada). Ignora nomes desconhecidos."""
        self._tools.pop(name, None)
        if self._allowed is not None:
            self._allowed.discard(name)

    def allow_only(self, names: set[str]) -> None:
        unknown = names - set(self._tools)
        if unknown:
            raise ValueError(
                f"Ferramentas desconhecidas na allowlist: {', '.join(sorted(unknown))}"
            )
        self._allowed = set(names)

    def allowed(self, name: str) -> bool:
        return name in self._tools and (self._allowed is None or name in self._allowed)

    def get(self, name: str) -> Tool:
        if not self.allowed(name):
            raise ToolNotAllowedError(f"Ferramenta '{name}' não está autorizada")
        return self._tools[name]

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for name, tool in sorted(self._tools.items()) if self.allowed(name)]

    def validate(self, call: ToolCall) -> tuple[Tool, BaseModel]:
        tool = self.get(call.name)
        try:
            return tool, tool.params.model_validate(call.arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'argumentos'}: {e['msg']}"
                for e in exc.errors()
            )
            raise ToolValidationError(
                f"Argumentos inválidos para '{call.name}': {problems}"
            ) from exc

    async def execute(
        self, call: ToolCall, params: BaseModel | None = None, *, retry: bool = False
    ) -> dict[str, Any]:
        """Executa uma chamada já validada (ou valida agora). Não repete o mesmo ``call.id``.

        ``retry`` só é usado pela política de recuperação, quando a falha anterior foi
        classificada como segura para repetir (nada chegou a ser feito).
        """
        tool, validated = (self._tools[call.name], params) if params else self.validate(call)
        if call.id in self._executed and not tool.idempotent and not retry:
            raise DuplicateCallError(f"A chamada '{call.id}' ({call.name}) já foi executada")
        self._executed.add(call.id)
        try:
            return await asyncio.wait_for(tool.handler(validated), tool.timeout)
        except TimeoutError as exc:
            raise ToolError(f"'{call.name}' excedeu {tool.timeout:.0f} s") from exc
