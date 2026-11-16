"""Teclado e mouse serializados pela trava global e respeitando o usuário (RF-006, RF-010)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from alfred.automation import input as win_input
from alfred.core.action_log import ActionLog
from alfred.core.input_lock import InputLock
from alfred.domain.module import Module

logger = logging.getLogger(__name__)

USER_IDLE_REQUIRED_MS = 1500
"""Só injeta input se o usuário estiver parado há pelo menos este tempo."""
OWN_INPUT_TOLERANCE_MS = 250
"""Se o último input do sistema coincide com a nossa última injeção, ele foi do ALFRED."""


class UserBusyError(RuntimeError):
    """O usuário continuou usando teclado/mouse; a ação foi abortada para não disputar com ele."""


@dataclass
class InputBackend:
    type_text: Callable[[str], None] = win_input.type_text
    press_hotkey: Callable[[str], None] = win_input.press_hotkey
    click: Callable[[int, int, str], None] = win_input.click
    idle_ms: Callable[[], int] = win_input.idle_ms
    now_ms: Callable[[], int] = win_input.monotonic_ms


class InputService(Module):
    name = "input"
    depends_on = ("database",)
    optional = True

    def __init__(
        self,
        lock: InputLock,
        action_log: ActionLog,
        backend: InputBackend | None = None,
        *,
        wait_for_user: float = 10.0,
        poll: float = 0.25,
    ) -> None:
        self.lock = lock
        self._log = action_log
        self._backend = backend or InputBackend()
        self._wait_for_user = wait_for_user
        self._poll = poll
        self._last_injection_ms: int | None = None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def _wait_user_idle(self) -> None:
        """Pausa enquanto o usuário usa teclado/mouse (o input do próprio ALFRED é descontado)."""
        waited = 0.0
        while self._user_active():
            if waited >= self._wait_for_user:
                raise UserBusyError(
                    "Você continuou usando o teclado/mouse; ação cancelada para não atrapalhar"
                )
            await asyncio.sleep(self._poll)
            waited += self._poll

    def _user_active(self) -> bool:
        idle = self._backend.idle_ms()
        if idle >= USER_IDLE_REQUIRED_MS:
            return False
        if self._last_injection_ms is None:
            return True
        since_own = self._backend.now_ms() - self._last_injection_ms
        return abs(idle - since_own) > OWN_INPUT_TOLERANCE_MS

    async def _run(self, action: str, params: dict[str, Any], op: Callable[[], None]) -> None:
        async with (
            self._log.record("automation", action, params=params),
            self.lock.hold(action),
        ):
            await self._wait_user_idle()
            await asyncio.to_thread(op)
            self._last_injection_ms = self._backend.now_ms()

    async def type_text(self, text: str) -> None:
        await self._run("digitar_texto", {"texto": text}, lambda: self._backend.type_text(text))

    async def press(self, hotkey: str) -> None:
        await self._run(
            "atalho_teclado", {"atalho": hotkey}, lambda: self._backend.press_hotkey(hotkey)
        )

    async def click(self, x: int, y: int, button: str = "left") -> None:
        await self._run(
            "clique_mouse",
            {"x": x, "y": y, "botao": button},
            lambda: self._backend.click(x, y, button),
        )
