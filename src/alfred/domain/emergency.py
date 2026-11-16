"""Contratos da interrupção de emergência (RF-015)."""

from __future__ import annotations

from typing import Protocol

from alfred.domain.events import Event


class EmergencyStopped(Event):
    """Todas as tarefas foram canceladas e o input liberado."""

    trigger: str
    cancelled_tasks: int
    stuck_tasks: int
    released_inputs: tuple[str, ...]
    elapsed_ms: float


class InputReleaser(Protocol):
    def release_all(self) -> list[str]:
        """Solta teclas modificadoras e botões do mouse pressionados; devolve o que soltou."""
        ...
