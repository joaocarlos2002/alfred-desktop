"""Automação de navegador com Playwright (RF-006).

Usa o Microsoft Edge já instalado (``channel="msedge"``), sem baixar navegadores, com um **perfil
dedicado do ALFRED** (nunca o perfil pessoal do usuário). O navegador abre sob demanda e fecha após
um período sem uso. Enviar formulários exige confirmação explícita (seção 6).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from alfred.core.action_log import ActionLog
from alfred.core.confirmations import ConfirmationService
from alfred.domain.confirmation import ConfirmationDecision
from alfred.domain.module import Health, HealthStatus, Module

logger = logging.getLogger(__name__)


class SubmissionDeniedError(PermissionError):
    pass


class BrowserService(Module):
    name = "browser"
    depends_on = ("database", "confirmations")
    optional = True

    def __init__(
        self,
        action_log: ActionLog,
        confirmations: ConfirmationService | None,
        profile_dir: Path,
        *,
        headless: bool = False,
        channel: str | None = "msedge",
        idle_close_seconds: float = 300.0,
        timeout_ms: int = 15_000,
    ) -> None:
        self._log = action_log
        self._confirmations = confirmations
        self.profile_dir = profile_dir
        self._headless = headless
        self._channel = channel
        self._idle_close = idle_close_seconds
        self._timeout_ms = timeout_ms
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._context is not None

    async def start(self) -> None:
        return None  # o navegador só abre quando for usado

    async def stop(self) -> None:
        await self.close_browser()

    async def health(self) -> Health:
        return Health(
            status=HealthStatus.HEALTHY, detail="aberto" if self.running else "sob demanda"
        )

    # ------------------------------------------------------------------ ciclo do navegador
    async def _ensure_page(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright

        if self._playwright is None:
            self._playwright = await async_playwright().start()
        await asyncio.to_thread(self.profile_dir.mkdir, parents=True, exist_ok=True)
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir), channel=self._channel, headless=self._headless
        )
        self._context.set_default_timeout(self._timeout_ms)
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        logger.info("Navegador do ALFRED aberto (perfil dedicado em %s)", self.profile_dir)
        return self._page

    def _touch(self) -> None:
        self._cancel_idle()
        self._idle_task = asyncio.ensure_future(self._close_when_idle())

    async def _close_when_idle(self) -> None:
        await asyncio.sleep(self._idle_close)
        # Mesma trava das ações: nunca fecha o navegador no meio de uma ação em andamento.
        async with self._lock:
            logger.info("Fechando o navegador após %.0f s sem uso", self._idle_close)
            await self._shutdown()

    def _cancel_idle(self) -> None:
        if self._idle_task is not None and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None

    async def close_browser(self) -> None:
        self._cancel_idle()
        async with self._lock:
            await self._shutdown()

    async def _shutdown(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._context = self._page = self._playwright = None

    async def _act(self, action: str, params: dict[str, Any], work: Any) -> Any:
        async with self._log.record("browser", action, params=params) as rec, self._lock:
            self._cancel_idle()  # uma ação em andamento adia o fechamento por inatividade
            page = await self._ensure_page()
            result = await work(page)
            rec.result = {"url": page.url, **(result if isinstance(result, dict) else {})}
            self._touch()
            return result

    # ------------------------------------------------------------------ ações
    async def navigate(self, url: str) -> dict[str, Any]:
        async def work(page: Any) -> dict[str, Any]:
            response = await page.goto(url)
            return {"titulo": await page.title(), "status": response.status if response else None}

        result: dict[str, Any] = await self._act("navegar", {"url": url}, work)
        return result

    async def click(self, selector: str) -> None:
        await self._act("clicar", {"seletor": selector}, lambda page: page.click(selector))

    async def fill(self, selector: str, value: str) -> None:
        await self._act(
            "preencher",
            {"seletor": selector, "valor": value},
            lambda page: page.fill(selector, value),
        )

    async def extract_text(self, selector: str = "body") -> str:
        async def work(page: Any) -> dict[str, Any]:
            return {"texto": (await page.inner_text(selector)).strip()}

        result: dict[str, Any] = await self._act("extrair_texto", {"seletor": selector}, work)
        return str(result["texto"])

    async def screenshot(self) -> bytes:
        async def work(page: Any) -> dict[str, Any]:
            return {"bytes": await page.screenshot()}

        result: dict[str, Any] = await self._act("capturar_pagina", {}, work)
        return bytes(result["bytes"])

    async def submit(self, selector: str, *, description: str) -> None:
        """Envia um formulário (clica em ``selector``) somente após confirmação explícita."""
        page_url = self._page.url if self._page is not None else "(página atual)"
        decision = ConfirmationDecision.DENIED
        if self._confirmations is not None:
            decision = await self._confirmations.request(
                action="Enviar formulário",
                target=f"{description} — {page_url}",
                impact="O envio pode não ser reversível (mensagens, cadastros, compras).",
                category="external_actions",
            )
        if decision is not ConfirmationDecision.APPROVED:
            async with self._log.record(
                "browser", "enviar_formulario", params={"seletor": selector}
            ) as rec:
                rec.deny("envio não confirmado")
            raise SubmissionDeniedError("Envio do formulário não confirmado")
        await self._act(
            "enviar_formulario", {"seletor": selector}, lambda page: page.click(selector)
        )
