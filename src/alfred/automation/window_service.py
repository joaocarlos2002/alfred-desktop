"""Serviço de janelas com registro de ações e comandos de voz (RF-006)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from alfred.automation import window_control
from alfred.automation.apps import normalize
from alfred.automation.window_control import Rect
from alfred.automation.windows import WindowInfo, list_windows
from alfred.core.action_log import ActionLog
from alfred.core.commands import CommandRegistry, CommandResult
from alfred.domain.module import Module

_TARGET = r"\s+(?:a |o |as |os )?(?:janela d[oa] |janela )?(?P<target>.+)"


class WindowNotFoundError(LookupError):
    pass


@dataclass
class WindowOps:
    """Operações injetáveis (Win32 por padrão; falsas nos testes)."""

    list: Callable[[], list[WindowInfo]] = list_windows
    focus: Callable[[int], bool] = window_control.focus
    minimize: Callable[[int], None] = window_control.minimize
    maximize: Callable[[int], None] = window_control.maximize
    restore: Callable[[int], None] = window_control.restore
    move_resize: Callable[[int, Rect], None] = window_control.move_resize
    get_rect: Callable[[int], Rect] = window_control.get_rect
    extra: dict[str, Any] = field(default_factory=dict)


def find_window(windows: list[WindowInfo], query: str) -> WindowInfo:
    """Por título ou nome do processo; prefere correspondência exata do título."""
    wanted = normalize(query)
    exact = [w for w in windows if normalize(w.title) == wanted]
    if exact:
        return exact[0]
    partial = [
        w
        for w in windows
        if wanted in normalize(w.title) or wanted == normalize(w.process).removesuffix(".exe")
    ]
    if not partial:
        raise WindowNotFoundError(f"Nenhuma janela corresponde a '{query}'")
    return partial[0]


class WindowService(Module):
    name = "windows"
    depends_on = ("database",)
    optional = True

    def __init__(self, action_log: ActionLog, ops: WindowOps | None = None) -> None:
        self._log = action_log
        self.ops = ops or WindowOps()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def _target(self, query: str) -> WindowInfo:
        return find_window(await asyncio.to_thread(self.ops.list), query)

    async def resolve(self, query: str) -> WindowInfo:
        """Janela que melhor corresponde a ``query`` (título ou processo)."""
        return await self._target(query)

    async def focus_window(self, query: str) -> dict[str, Any]:
        async with self._log.record("automation", "focus_window", params={"janela": query}) as rec:
            window = await self._target(query)
            focused = await asyncio.to_thread(self.ops.focus, window.hwnd)
            if not focused:
                raise window_control.WindowControlError(
                    f"O Windows não permitiu trazer '{window.title}' para frente"
                )
            rec.result = {"janela": window.title}
            return rec.result

    async def set_state(self, query: str, state: str) -> dict[str, Any]:
        operation = {
            "minimizar": self.ops.minimize,
            "maximizar": self.ops.maximize,
            "restaurar": self.ops.restore,
        }[state]
        async with self._log.record(
            "automation", f"{state}_janela", params={"janela": query}
        ) as rec:
            window = await self._target(query)
            await asyncio.to_thread(operation, window.hwnd)
            rec.result = {"janela": window.title}
            return rec.result

    async def move_window(self, query: str, rect: Rect) -> dict[str, Any]:
        params = {"janela": query, "x": rect.x, "y": rect.y, "w": rect.width, "h": rect.height}
        async with self._log.record("automation", "mover_janela", params=params) as rec:
            window = await self._target(query)
            await asyncio.to_thread(self.ops.move_resize, window.hwnd, rect)
            final = await asyncio.to_thread(self.ops.get_rect, window.hwnd)
            rec.result = {"janela": window.title, "retangulo": final.__dict__}
            return rec.result

    # ------------------------------------------------------------------ voz
    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register(r"(?:minimiz[ae]|minimizar)" + _TARGET, self._cmd("minimizar"))
        registry.register(r"(?:maximiz[ae]|maximizar)" + _TARGET, self._cmd("maximizar"))
        registry.register(r"(?:restaur[ae]|restaurar)" + _TARGET, self._cmd("restaurar"))
        registry.register(
            r"(?:mostr[ae]|mostrar|foc[ae]|focar|alterne para|v[aá] para)" + _TARGET,
            self._focus_cmd,
        )

    def _cmd(self, state: str) -> Callable[[re.Match[str]], Any]:
        async def handler(match: re.Match[str]) -> CommandResult:
            target = match["target"].strip(" .!?")
            try:
                result = await self.set_state(target, state)
            except WindowNotFoundError:
                return CommandResult(f"Não encontrei a janela {target}.", False, f"{state}_janela")
            past = {"minimizar": "minimizada", "maximizar": "maximizada", "restaurar": "restaurada"}
            return CommandResult(f"{result['janela']} {past[state]}.", True, f"{state}_janela")

        return handler

    async def _focus_cmd(self, match: re.Match[str]) -> CommandResult:
        target = match["target"].strip(" .!?")
        try:
            result = await self.focus_window(target)
        except WindowNotFoundError:
            return CommandResult(f"Não encontrei a janela {target}.", False, "focar_janela")
        except window_control.WindowControlError as exc:
            return CommandResult(str(exc), False, "focar_janela")
        return CommandResult(f"Pronto, {result['janela']}.", True, "focar_janela")
