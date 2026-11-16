"""Serviço UIA: ações em elementos com destaque no cursor virtual e registro (RF-006)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from alfred.automation.uia import ElementInfo, ElementQuery, UiaClient
from alfred.automation.window_service import find_window
from alfred.automation.windows import WindowInfo, list_windows
from alfred.core.action_log import ActionLog
from alfred.domain.module import Module
from alfred.domain.screen import VirtualCursor


class UiaBackend(Protocol):
    def invoke(self, hwnd: int, query: ElementQuery) -> ElementInfo: ...

    def set_text(self, hwnd: int, query: ElementQuery, text: str) -> ElementInfo: ...

    def select(self, hwnd: int, query: ElementQuery, item: str) -> ElementInfo: ...

    def expand(self, hwnd: int, query: ElementQuery) -> ElementInfo: ...

    def read_text(self, hwnd: int, query: ElementQuery) -> str: ...

    def tree(self, hwnd: int, limit: int = 200) -> list[ElementInfo]: ...

    def close(self) -> None: ...


class UiaService(Module):
    name = "uia"
    depends_on = ("database",)
    optional = True

    def __init__(
        self,
        action_log: ActionLog,
        backend: UiaBackend | None = None,
        *,
        windows: Callable[[], list[WindowInfo]] = list_windows,
        cursor: Callable[[], VirtualCursor | None] = lambda: None,
    ) -> None:
        self._log = action_log
        self._backend: UiaBackend = backend or UiaClient()
        self._windows = windows
        self._cursor = cursor

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self._backend.close()

    async def _hwnd(self, window: str) -> int:
        return find_window(await asyncio.to_thread(self._windows), window).hwnd

    def _point(self, info: ElementInfo, *, click: bool = False) -> None:
        cursor = self._cursor()
        if cursor is not None:
            cursor.point_at(info.rect, label=info.name or info.automation_id or None)
            if click:
                cursor.click()

    async def _act(
        self, action: str, window: str, query: ElementQuery, op: Callable[[int], Any], **params: Any
    ) -> Any:
        details = {"janela": window, "elemento": query.describe(), **params}
        async with self._log.record("automation", action, params=details) as rec:
            hwnd = await self._hwnd(window)
            result = await asyncio.to_thread(op, hwnd)
            if isinstance(result, ElementInfo):
                self._point(result, click=action == "uia_invoke")
                rec.result = {"elemento": result.name or result.automation_id}
            else:
                rec.result = {"texto": result}
            return result

    async def invoke(self, window: str, query: ElementQuery) -> ElementInfo:
        result: ElementInfo = await self._act(
            "uia_invoke", window, query, lambda h: self._backend.invoke(h, query)
        )
        return result

    async def set_text(self, window: str, query: ElementQuery, text: str) -> ElementInfo:
        result: ElementInfo = await self._act(
            "uia_set_text",
            window,
            query,
            lambda h: self._backend.set_text(h, query, text),
            texto=text,
        )
        return result

    async def select(self, window: str, query: ElementQuery, item: str) -> ElementInfo:
        result: ElementInfo = await self._act(
            "uia_select", window, query, lambda h: self._backend.select(h, query, item), item=item
        )
        return result

    async def expand(self, window: str, query: ElementQuery) -> ElementInfo:
        result: ElementInfo = await self._act(
            "uia_expand", window, query, lambda h: self._backend.expand(h, query)
        )
        return result

    async def read_text(self, window: str, query: ElementQuery) -> str:
        result: str = await self._act(
            "uia_read", window, query, lambda h: self._backend.read_text(h, query)
        )
        return result

    async def describe(self, window: str, limit: int = 200) -> list[dict[str, Any]]:
        """Árvore resumida (para o contexto da IA na Fase 5)."""
        hwnd = await self._hwnd(window)
        elements = await asyncio.to_thread(self._backend.tree, hwnd, limit)
        return [
            {
                "nome": e.name,
                "tipo": e.control_type,
                "id": e.automation_id,
                "habilitado": e.enabled,
            }
            for e in elements
        ]
