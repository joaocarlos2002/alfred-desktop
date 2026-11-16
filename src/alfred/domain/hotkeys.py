"""Contratos de atalhos globais (RF-003, RF-015)."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from alfred.domain.events import Event


class HotkeyAction(StrEnum):
    ACTIVATE = "activate"
    EMERGENCY = "emergency"


class HotkeyPressed(Event):
    action: HotkeyAction
    hotkey: str


class HotkeyRegistrationFailed(Event):
    """Um atalho não pôde ser registrado (normalmente já está em uso por outro programa)."""

    action: HotkeyAction
    hotkey: str
    reason: str


class HotkeyConflictError(Exception):
    """O atalho já está registrado por outro programa (ou por esta instância)."""


class HotkeyBackend(Protocol):
    """Mecanismo de registro de atalhos globais do sistema operacional.

    ``callback`` é chamado na thread do backend, não no event loop.
    """

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def register(self, hotkey: str, callback: Callable[[], None]) -> int:
        """Registra e devolve um id. Raises: ValueError, HotkeyConflictError."""
        ...

    def unregister(self, hotkey_id: int) -> None: ...
