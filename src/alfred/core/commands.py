"""Registro de comandos falados/escritos que módulos de camadas superiores podem atender.

O ``core`` não conhece a automação (fronteiras do ADR-0001); a automação se registra aqui com um
padrão e um handler assíncrono que devolve a resposta e se o comando foi cumprido.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    reply: str
    succeeded: bool = True
    tool: str = "comando"


type CommandHandler = Callable[[re.Match[str]], Awaitable[CommandResult]]


class CommandRegistry:
    def __init__(self) -> None:
        self._handlers: list[tuple[re.Pattern[str], CommandHandler]] = []

    def register(self, pattern: str | re.Pattern[str], handler: CommandHandler) -> None:
        compiled = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
        self._handlers.append((compiled, handler))

    def match(self, command: str) -> tuple[re.Match[str], CommandHandler] | None:
        for pattern, handler in self._handlers:
            found = pattern.fullmatch(command)
            if found:
                return found, handler
        return None
