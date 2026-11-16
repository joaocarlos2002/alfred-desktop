"""Ferramentas de automação oferecidas à IA (registradas na raiz de composição)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from alfred.automation.browser import BrowserService
from alfred.automation.input_service import InputService
from alfred.automation.service import AppsService
from alfred.automation.uia import ElementQuery
from alfred.automation.uia_service import UiaService
from alfred.automation.window_service import WindowService
from alfred.core.scheduler import INPUT
from alfred.core.tools import Risk, Tool, ToolRegistry


class AppParams(BaseModel):
    nome: str = Field(min_length=1, description="Nome do aplicativo, como aparece no Menu Iniciar")


class WindowParams(BaseModel):
    janela: str = Field(min_length=1, description="Título (ou parte) ou processo da janela")


class WindowStateParams(WindowParams):
    estado: str = Field(pattern="^(minimizar|maximizar|restaurar)$")


class ElementParams(WindowParams):
    automation_id: str | None = Field(default=None, description="AutomationId do elemento")
    nome_elemento: str | None = Field(default=None, description="Nome visível do elemento")
    tipo: str | None = Field(default=None, description="Tipo de controle (Button, Edit...)")

    def query(self) -> ElementQuery:
        return ElementQuery(self.automation_id, self.nome_elemento, self.tipo)


class SetTextParams(ElementParams):
    texto: str


class TypeParams(BaseModel):
    texto: str = Field(min_length=1, description="Texto a digitar na janela em foco")


class KeyParams(BaseModel):
    atalho: str = Field(min_length=1, description="Ex.: enter, ctrl+s, alt+f4")


class UrlParams(BaseModel):
    url: str = Field(pattern="^(https?|file)://", description="Endereço completo")


class SelectorParams(BaseModel):
    seletor: str = Field(default="body", description="Seletor CSS")


def register_automation_tools(
    registry: ToolRegistry,
    *,
    apps: AppsService,
    windows: WindowService,
    uia: UiaService,
    keyboard: InputService,
    browser: BrowserService,
) -> None:
    async def open_app(p: AppParams) -> dict[str, Any]:
        return await apps.open_app(p.nome)

    async def close_app(p: AppParams) -> dict[str, Any]:
        return await apps.close_app(p.nome)

    async def focus(p: WindowParams) -> dict[str, Any]:
        return await windows.focus_window(p.janela)

    async def window_state(p: WindowStateParams) -> dict[str, Any]:
        return await windows.set_state(p.janela, p.estado)

    async def invoke(p: ElementParams) -> dict[str, Any]:
        info = await uia.invoke(p.janela, p.query())
        return {"elemento": info.name or info.automation_id}

    async def set_text(p: SetTextParams) -> dict[str, Any]:
        info = await uia.set_text(p.janela, p.query(), p.texto)
        return {"elemento": info.name or info.automation_id}

    async def read_text(p: ElementParams) -> dict[str, Any]:
        return {"texto": await uia.read_text(p.janela, p.query())}

    async def describe(p: WindowParams) -> dict[str, Any]:
        return {"elementos": await uia.describe(p.janela, limit=150)}

    async def type_text(p: TypeParams) -> dict[str, Any]:
        await keyboard.type_text(p.texto)
        return {"digitado": len(p.texto)}

    async def press(p: KeyParams) -> dict[str, Any]:
        await keyboard.press(p.atalho)
        return {"atalho": p.atalho}

    async def navigate(p: UrlParams) -> dict[str, Any]:
        return await browser.navigate(p.url)

    async def page_text(p: SelectorParams) -> dict[str, Any]:
        text = await browser.extract_text(p.seletor)
        return {"texto": text[:4000]}

    tools = [
        Tool(
            "abrir_aplicativo",
            "Abre um aplicativo instalado pelo nome.",
            AppParams,
            open_app,
            "applications",
            resource=INPUT,
        ),
        Tool(
            "fechar_aplicativo",
            "Fecha um aplicativo (pede confirmação se precisar forçar).",
            AppParams,
            close_app,
            "applications",
            resource=INPUT,
        ),
        Tool(
            "focar_janela",
            "Traz uma janela para frente.",
            WindowParams,
            focus,
            "windows",
            resource=INPUT,
        ),
        Tool(
            "estado_janela",
            "Minimiza, maximiza ou restaura uma janela.",
            WindowStateParams,
            window_state,
            "windows",
            resource=INPUT,
        ),
        Tool(
            "clicar_elemento",
            "Aciona um botão/elemento via UI Automation (sem coordenadas).",
            ElementParams,
            invoke,
            "ui",
            resource=INPUT,
        ),
        Tool(
            "preencher_elemento",
            "Define o texto de um campo via UI Automation.",
            SetTextParams,
            set_text,
            "ui",
            resource=INPUT,
        ),
        Tool(
            "ler_elemento",
            "Lê o texto de um elemento da interface.",
            ElementParams,
            read_text,
            "ui",
            risk=Risk.READ,
            idempotent=True,
        ),
        Tool(
            "descrever_janela",
            "Lista os elementos (nome, tipo, id) de uma janela.",
            WindowParams,
            describe,
            "ui",
            risk=Risk.READ,
            idempotent=True,
        ),
        Tool(
            "digitar_texto",
            "Digita texto na janela em foco.",
            TypeParams,
            type_text,
            "input",
            resource=INPUT,
        ),
        Tool(
            "pressionar_teclas",
            "Pressiona um atalho de teclado na janela em foco.",
            KeyParams,
            press,
            "input",
            resource=INPUT,
        ),
        Tool(
            "abrir_pagina",
            "Abre um endereço no navegador do ALFRED.",
            UrlParams,
            navigate,
            "browser",
            resource="browser",
        ),
        Tool(
            "ler_pagina",
            "Lê o texto da página atual do navegador.",
            SelectorParams,
            page_text,
            "browser",
            risk=Risk.READ,
            idempotent=True,
            resource="browser",
        ),
    ]
    for tool in tools:
        registry.register(tool)
