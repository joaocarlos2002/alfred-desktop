"""Serviço de aplicações: abrir e fechar com registro e confirmação (RF-006, seção 6)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from alfred.automation.apps import (
    AmbiguousAppError,
    AppCatalog,
    AppNotFoundError,
    StartApp,
    launch_start_app,
    normalize,
)
from alfred.automation.windows import (
    WindowInfo,
    list_windows,
    matches_app,
    request_close,
    terminate,
    wait_until_closed,
)
from alfred.core.action_log import ActionLog
from alfred.core.commands import CommandRegistry, CommandResult
from alfred.core.confirmations import ConfirmationService
from alfred.domain.confirmation import ConfirmationDecision
from alfred.domain.module import Module

logger = logging.getLogger(__name__)


class AliasStore(Protocol):
    async def list_aliases(self) -> dict[str, list[str]]: ...

    async def add_alias(self, app_name: str, alias: str) -> None: ...


_ARTICLE = r"\s+(?:o |a |os |as )?(?P<app>.+)"
OPEN_PATTERN = r"(?:abr[ae]|abrir|inici[ae]|iniciar|execut[ae]|executar|ligue|ligar)" + _ARTICLE
CLOSE_PATTERN = r"(?:fech[ae]|fechar|encerr[ae]|encerrar)" + _ARTICLE


class AppsService(Module):
    name = "apps"
    depends_on = ("database", "confirmations")
    optional = True

    def __init__(
        self,
        catalog: AppCatalog,
        action_log: ActionLog,
        confirmations: ConfirmationService | None,
        *,
        launcher: Callable[[StartApp], None] = launch_start_app,
        windows: Callable[[], list[WindowInfo]] = list_windows,
        closer: Callable[[int], None] = request_close,
        killer: Callable[[list[int]], None] = terminate,
        waiter: Callable[[list[int], float], list[int]] = wait_until_closed,
        open_timeout: float = 10.0,
        close_timeout: float = 5.0,
        aliases: AliasStore | None = None,
    ) -> None:
        self._aliases = aliases
        self.catalog = catalog
        self._log = action_log
        self._confirmations = confirmations
        self._launcher = launcher
        self._windows = windows
        self._closer = closer
        self._killer = killer
        self._waiter = waiter
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout

    async def start(self) -> None:
        if self._aliases is not None:
            self.catalog.aliases = await self._aliases.list_aliases()
        await asyncio.to_thread(self.catalog.refresh)

    async def add_alias(self, app_name: str, alias: str) -> None:
        """Ex.: ``add_alias("Calculadora", "calc")`` — persistido em ``applications``."""
        if self._aliases is None:
            raise RuntimeError("Aliases indisponíveis sem banco de dados")
        await self._aliases.add_alias(app_name, alias)
        self.catalog.aliases.setdefault(app_name, []).append(alias)

    async def stop(self) -> None:
        return None

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register(OPEN_PATTERN, self._open_command)
        registry.register(CLOSE_PATTERN, self._close_command)

    # ------------------------------------------------------------------ abrir
    async def open_app(self, query: str) -> dict[str, Any]:
        async with self._log.record("automation", "open_app", params={"app": query}) as rec:
            app = await asyncio.to_thread(self.catalog.resolve, query)
            before = {w.hwnd for w in await asyncio.to_thread(self._windows)}
            await asyncio.to_thread(self._launcher, app)
            window = await asyncio.to_thread(self._wait_for_window, app, before)
            rec.result = {"app": app.name, "window": window.title if window else None}
            return rec.result

    def _wait_for_window(self, app: StartApp, before: set[int]) -> WindowInfo | None:
        deadline = time.monotonic() + self._open_timeout
        while time.monotonic() < deadline:
            for window in self._windows():
                if matches_app(window, app.name, normalize) and window.hwnd not in before:
                    return window
            time.sleep(0.2)
        return next((w for w in self._windows() if matches_app(w, app.name, normalize)), None)

    # ------------------------------------------------------------------ fechar
    async def close_app(self, query: str) -> dict[str, Any]:
        async with self._log.record("automation", "close_app", params={"app": query}) as rec:
            app = await asyncio.to_thread(self.catalog.resolve, query)
            windows = [
                w
                for w in await asyncio.to_thread(self._windows)
                if matches_app(w, app.name, normalize)
            ]
            if not windows:
                rec.result = {"app": app.name, "closed": 0, "message": "não estava aberto"}
                return rec.result
            for window in windows:
                await asyncio.to_thread(self._closer, window.hwnd)
            remaining = await asyncio.to_thread(
                self._waiter, [w.hwnd for w in windows], self._close_timeout
            )
            if not remaining:
                rec.result = {"app": app.name, "closed": len(windows), "forced": False}
                return rec.result
            stuck = [w for w in windows if w.hwnd in remaining]
            if not await self._confirm_force(app, stuck):
                rec.deny("encerramento forçado não confirmado")
                rec.result = {"app": app.name, "closed": len(windows) - len(stuck), "forced": False}
                return rec.result
            await asyncio.to_thread(self._killer, [w.pid for w in stuck])
            rec.result = {"app": app.name, "closed": len(windows), "forced": True}
            return rec.result

    async def _confirm_force(self, app: StartApp, stuck: list[WindowInfo]) -> bool:
        if self._confirmations is None:
            return False
        decision = await self._confirmations.request(
            action=f"Forçar o encerramento de {app.name}",
            target=", ".join(w.title for w in stuck),
            impact="Alterações não salvas nessas janelas serão perdidas.",
            category="applications",
        )
        return decision is ConfirmationDecision.APPROVED

    # ------------------------------------------------------------------ comandos de voz
    async def _open_command(self, match: re.Match[str]) -> CommandResult:
        return await self._run_command("abrir", match["app"], self.open_app)

    async def _close_command(self, match: re.Match[str]) -> CommandResult:
        return await self._run_command("fechar", match["app"], self.close_app)

    async def _run_command(self, verb: str, query: str, action: Any) -> CommandResult:
        tool = "abrir_aplicativo" if verb == "abrir" else "fechar_aplicativo"
        query = query.strip(" .!?")
        try:
            result = await action(query)
        except AmbiguousAppError as exc:
            options = ", ".join(c.name for c in exc.candidates[:3])
            return CommandResult(f"Qual deles? Encontrei: {options}.", False, tool)
        except AppNotFoundError:
            return CommandResult(f"Não encontrei nenhum aplicativo chamado {query}.", False, tool)
        if verb == "abrir":
            return CommandResult(f"Abrindo {result['app']}.", True, tool)
        if result.get("closed", 0) == 0 and result.get("message"):
            return CommandResult(f"{result['app']} não estava aberto.", True, tool)
        if result.get("forced") is False and result.get("closed", 0) == 0:
            return CommandResult(f"Não fechei {result['app']}.", False, tool)
        return CommandResult(f"{result['app']} fechado.", True, tool)
