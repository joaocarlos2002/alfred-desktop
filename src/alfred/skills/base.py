"""Contrato de skill (#123): uma capacidade empacotada que o usuário liga e desliga.

Uma skill contribui ferramentas (usadas pelo orquestrador, sempre com allowlist, validação e
permissões — ADR-0002), frases de voz atendidas sem IA e um ciclo de vida próprio. Ela nunca
executa nada por fora do orquestrador ou do registro de comandos.
"""

from __future__ import annotations

from typing import ClassVar

from alfred.core.commands import CommandHandler
from alfred.core.tools import Tool


class Skill:
    key: ClassVar[str]
    """Identificador estável (persistido em ``skills.enabled``)."""
    name: ClassVar[str]
    description: ClassVar[str]

    def tools(self) -> list[Tool]:
        """Ferramentas oferecidas à IA enquanto a skill estiver ativa."""
        return []

    def commands(self) -> list[tuple[str, CommandHandler]]:
        """Frases de voz (regex sobre o texto normalizado) atendidas diretamente."""
        return []

    async def activate(self) -> None:
        """Prepara recursos. Uma exceção aqui desativa só esta skill."""
        return None

    async def deactivate(self) -> None:
        """Libera recursos (chamado ao desligar a skill ou encerrar o ALFRED)."""
        return None
