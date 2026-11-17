"""Skill "aprender sobre os programas" (#125)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from alfred.core.tools import ToolRegistry
from alfred.domain.ai import ToolCall
from alfred.infrastructure.db.app_knowledge_repository import SqlAppKnowledgeRepository
from alfred.infrastructure.db.database import Database
from alfred.skills.app_knowledge import AppKnowledgeSkill, app_key, extract_shortcuts


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "alfred.db")
    await db.start()
    yield db
    await db.stop()


class FakeWindow:
    def __init__(self, title: str, process: str) -> None:
        self.title = title
        self.process = process
        self.hwnd = 1


class FakeWindows:
    def __init__(self) -> None:
        self.windows = {"word": FakeWindow("Documento1 - Word", "WINWORD.EXE")}

    async def resolve(self, query: str) -> FakeWindow:
        for key, window in self.windows.items():
            if key in query.casefold():
                return window
        raise LookupError(query)


class FakeUia:
    def __init__(self) -> None:
        self.described: list[str] = []

    async def describe(self, window: str, limit: int = 200) -> list[dict[str, Any]]:
        self.described.append(window)
        return [
            {"nome": "Salvar (Ctrl+S)", "tipo": "Button", "id": "FileSave", "habilitado": True},
            {"nome": "Exportar", "tipo": "MenuItem", "id": "", "habilitado": True},
            {"nome": "Exportar", "tipo": "MenuItem", "id": "", "habilitado": True},  # duplicado
            {"nome": "", "tipo": "Pane", "id": "", "habilitado": True},  # sem nome: descartado
            {"nome": "Desfazer   Ctrl+Z", "tipo": "Button", "id": "Undo", "habilitado": True},
        ]


def test_shortcuts_and_app_keys() -> None:
    assert extract_shortcuts(
        ["Salvar (Ctrl+S)", "Desfazer   Ctrl+Z", "Abrir", "Tela cheia F11"]
    ) == [
        "Ctrl+S",
        "Ctrl+Z",
        "F11",
    ]
    assert app_key("WINWORD.EXE") == "winword.exe"
    assert app_key("Calculator") == "calculator.exe"


async def skill(database: Database) -> tuple[AppKnowledgeSkill, ToolRegistry, FakeUia]:
    uia = FakeUia()
    s = AppKnowledgeSkill(SqlAppKnowledgeRepository(database), FakeWindows(), uia)  # type: ignore[arg-type]
    await s.activate()
    tools = ToolRegistry()
    for tool in s.tools():
        tools.register(tool)
    return s, tools, uia


async def run(tools: ToolRegistry, name: str, **args: Any) -> dict[str, Any]:
    call = ToolCall(
        id=f"{name}-{len(args)}-{json.dumps(args, sort_keys=True)}", name=name, arguments=args
    )
    tool, params = tools.validate(call)
    return await tool.handler(params)


async def test_learning_a_program_builds_a_persistent_map(database: Database) -> None:
    _s, tools, uia = await skill(database)
    learned = await run(tools, "aprender_programa", janela="Word")
    assert learned["programa"] == "winword.exe"
    assert learned["elementos"] == 3  # duplicados e sem nome descartados
    assert learned["atalhos"] == ["Ctrl+S", "Ctrl+Z"]
    assert uia.described == ["Word"]  # só leitura da árvore UIA

    known = await run(tools, "consultar_programa", programa="Word")
    names = [e["nome"] for e in known["elementos"]]
    assert names == ["Salvar (Ctrl+S)", "Exportar", "Desfazer   Ctrl+Z"]

    # outra instância (ex.: depois de reiniciar) enxerga o mesmo conhecimento
    again = AppKnowledgeSkill(SqlAppKnowledgeRepository(database), FakeWindows(), FakeUia())  # type: ignore[arg-type]
    assert "Word" in await again.context()


async def test_user_taught_procedures_are_saved_and_returned(database: Database) -> None:
    s, tools, _ = await skill(database)
    await run(
        tools,
        "ensinar_procedimento",
        programa="Word",
        nome="exportar PDF",
        passos="Arquivo > Exportar > Criar PDF/XPS e confirmar",
    )
    known = await run(tools, "consultar_programa", programa="winword")
    assert known["procedimentos"] == {
        "exportar PDF": "Arquivo > Exportar > Criar PDF/XPS e confirmar"
    }
    assert "exportar PDF" in await s.context()


async def test_tools_are_safe_by_construction(database: Database) -> None:
    _, tools, _ = await skill(database)
    learn = tools.get("aprender_programa")
    consult = tools.get("consultar_programa")
    teach = tools.get("ensinar_procedimento")
    assert learn.untrusted_output  # o que a tela do programa diz é dado, não instrução
    assert consult.untrusted_output
    assert learn.needs_confirmation is not None
    first = learn.needs_confirmation(learn.params.model_validate({"janela": "Word"}))
    assert first is not None
    assert "somente leitura" in first  # permissão por programa, explicando que só lê
    assert (
        teach.risk.value != "read"
    )  # gravar conhecimento é uma ação (escala após conteúdo externo)


async def test_permission_is_asked_once_per_program(database: Database) -> None:
    _s, tools, _ = await skill(database)
    learn = tools.get("aprender_programa")
    params = learn.params.model_validate({"janela": "Word"})
    assert learn.needs_confirmation(params) is not None
    await run(tools, "aprender_programa", janela="Word")
    assert learn.needs_confirmation(params) is None  # já aprovado: reexplorar não pergunta de novo


async def test_forget_program(database: Database) -> None:
    s, tools, _ = await skill(database)
    await run(tools, "aprender_programa", janela="Word")
    assert [k.app_key for k in await s.programs()] == ["winword.exe"]
    await s.forget("winword.exe")
    assert await s.programs() == []
    assert await s.context() == ""


async def test_learned_programs_reach_the_planner_through_skill_context(
    database: Database, tmp_path: Path
) -> None:
    from alfred.config.store import SettingsStore
    from alfred.core.commands import CommandRegistry
    from alfred.core.event_bus import EventBus
    from alfred.core.settings_service import SettingsService
    from alfred.skills.manager import SkillManager

    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), EventBus())
    await settings.start()
    await settings.update("skills", enabled=("programas",))
    s = AppKnowledgeSkill(SqlAppKnowledgeRepository(database), FakeWindows(), FakeUia())  # type: ignore[arg-type]
    manager = SkillManager(settings, ToolRegistry(), CommandRegistry())
    manager.register(s)
    await manager.start()
    assert await manager.context() == ""  # nada aprendido ainda
    await s.tools()[0].handler(s.tools()[0].params.model_validate({"janela": "Word"}))
    context = await manager.context()
    assert "Word (winword.exe)" in context
    assert "consultar_programa" in context
    await manager.set_enabled("programas", False)
    assert await manager.context() == ""  # skill desligada não fala nada ao modelo
    await manager.stop()


def test_applications_section_lists_and_forgets(database: Database) -> None:
    pytest.importorskip("PySide6")
    from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
    from alfred.ui.settings_window import ApplicationsSection

    ensure_qt_application()

    class Manager:
        def __init__(self, skill: AppKnowledgeSkill) -> None:
            self.skill = skill

        def get(self, key: str) -> AppKnowledgeSkill:
            return self.skill

    async def scenario() -> tuple[int, str, int]:
        s = AppKnowledgeSkill(SqlAppKnowledgeRepository(database), FakeWindows(), FakeUia())  # type: ignore[arg-type]
        await s.activate()
        tool = s.tools()[0]
        await tool.handler(tool.params.model_validate({"janela": "Word"}))
        section = ApplicationsSection(lambda: Manager(s))
        await section.reload()
        listed = section.list.count()
        section.list.setCurrentRow(0)
        details = section.details.toPlainText()
        await section._forget()
        return listed, details, section.list.count()

    listed, details, after = run_on_qt(scenario())
    assert listed == 1
    assert "Ctrl+S" in details
    assert after == 0


@pytest.mark.windows
async def test_real_app_is_learned_and_the_map_completes_a_task(database: Database) -> None:
    """Aplicação de teste UIA real: explorar (só leitura) → mapa → usar o mapa para agir."""
    import asyncio
    import os
    import subprocess
    import sys
    import time
    import uuid

    if sys.platform != "win32":
        pytest.skip("UI Automation do Windows")
    from alfred.automation.uia import ElementQuery
    from alfred.automation.uia_service import UiaService
    from alfred.automation.window_service import WindowService
    from alfred.automation.windows import list_windows
    from alfred.core.action_log import ActionLog

    class Activity:
        async def add(self, entry: Any) -> Any:
            return entry

    title = f"ALFRED Teste Programa {uuid.uuid4().hex[:6]}"
    app = Path(__file__).resolve().parent / "fixtures" / "uia_app.py"
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    proc = await asyncio.to_thread(
        subprocess.Popen, [sys.executable, str(app), title, "origem"], env=env
    )
    try:
        deadline = time.monotonic() + 15
        while not any(title in w.title for w in list_windows()):
            assert time.monotonic() < deadline, "janela de teste não apareceu"
            await asyncio.sleep(0.2)
        log = ActionLog(Activity())  # type: ignore[arg-type]
        uia = UiaService(log)
        s = AppKnowledgeSkill(SqlAppKnowledgeRepository(database), WindowService(log), uia)
        await s.activate()
        tools = ToolRegistry()
        for tool in s.tools():
            tools.register(tool)
        learned = await run(tools, "aprender_programa", janela=title)
        assert learned["elementos"] >= 4
        known = await run(tools, "consultar_programa", programa=title)
        by_name = {e["nome"]: e for e in known["elementos"]}
        assert {"Entrada", "Confirmar", "Resultado"} <= set(by_name)
        # o mapa diz onde escrever e o que clicar: nenhum palpite, só UIA
        entrada, confirmar = by_name["Entrada"], by_name["Confirmar"]
        await uia.set_text(title, ElementQuery(name=entrada["nome"]), "aprendido")
        await uia.invoke(title, ElementQuery(name=confirmar["nome"]))
        result = await uia.read_text(title, ElementQuery(name="Resultado"))
        assert result == "recebido: aprendido"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
