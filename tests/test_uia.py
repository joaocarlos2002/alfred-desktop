from __future__ import annotations

import sys
from typing import Any

import pytest

from alfred.automation.uia import ElementInfo, ElementNotFoundError, ElementQuery
from alfred.automation.uia_service import UiaService
from alfred.automation.windows import WindowInfo
from alfred.core.action_log import ActionLog
from alfred.domain.activity import ActivityEntry
from alfred.domain.screen import ScreenRect


class MemoryActivity:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        self.entries.append(entry)
        return entry


BUTTON = ElementInfo(
    "Salvar", "Button", "saveButton", ScreenRect(x=10, y=20, width=80, height=30), True
)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def invoke(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        if query.automation_id == "ausente":
            raise ElementNotFoundError("Elemento não encontrado (auto_id='ausente')")
        self.calls.append(("invoke", hwnd, query.automation_id))
        return BUTTON

    def set_text(self, hwnd: int, query: ElementQuery, text: str) -> ElementInfo:
        self.calls.append(("set_text", hwnd, text))
        return BUTTON

    def select(self, hwnd: int, query: ElementQuery, item: str) -> ElementInfo:
        return BUTTON

    def expand(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        return BUTTON

    def read_text(self, hwnd: int, query: ElementQuery) -> str:
        return "texto lido"

    def tree(self, hwnd: int, limit: int = 200) -> list[ElementInfo]:
        return [BUTTON]

    def close(self) -> None: ...


class FakeCursor:
    def __init__(self) -> None:
        self.pointed: list[tuple[ScreenRect, str | None]] = []
        self.clicks = 0

    def point_at(self, rect: ScreenRect, *, label: str | None = None) -> None:
        self.pointed.append((rect, label))

    def click(self) -> None:
        self.clicks += 1

    def move_to(self, x: int, y: int) -> None: ...

    def hide(self) -> None: ...


def service() -> tuple[UiaService, FakeBackend, FakeCursor, MemoryActivity]:
    backend, cursor, activity = FakeBackend(), FakeCursor(), MemoryActivity()
    svc = UiaService(
        ActionLog(activity),  # type: ignore[arg-type]
        backend,
        windows=lambda: [WindowInfo(7, "Documento - Editor", 1, "editor.exe")],
        cursor=lambda: cursor,
    )
    return svc, backend, cursor, activity


def test_query_criteria() -> None:
    query = ElementQuery(automation_id="num1Button", control_type="Button")
    assert query.criteria() == {"auto_id": "num1Button", "control_type": "Button"}
    assert "num1Button" in query.describe()


async def test_invoke_points_virtual_cursor_and_logs() -> None:
    svc, backend, cursor, activity = service()

    info = await svc.invoke("editor", ElementQuery(automation_id="saveButton"))

    assert info.name == "Salvar"
    assert backend.calls == [("invoke", 7, "saveButton")]
    assert cursor.pointed == [(BUTTON.rect, "Salvar")]
    assert cursor.clicks == 1
    assert activity.entries[0].action == "uia_invoke"
    assert activity.entries[0].status == "succeeded"


async def test_set_text_read_and_describe() -> None:
    svc, backend, _, activity = service()
    await svc.set_text("editor", ElementQuery(automation_id="campo"), "olá")
    assert await svc.read_text("editor", ElementQuery(automation_id="campo")) == "texto lido"
    tree = await svc.describe("editor")
    assert tree == [{"nome": "Salvar", "tipo": "Button", "id": "saveButton", "habilitado": True}]
    assert ("set_text", 7, "olá") in backend.calls
    assert [e.action for e in activity.entries] == ["uia_set_text", "uia_read"]


async def test_missing_element_is_a_clear_logged_failure() -> None:
    svc, _, cursor, activity = service()
    with pytest.raises(ElementNotFoundError, match="ausente"):
        await svc.invoke("editor", ElementQuery(automation_id="ausente"))
    assert activity.entries[-1].status == "failed"
    assert cursor.pointed == []


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
async def test_real_calculator_without_coordinates() -> None:
    from alfred.automation.apps import AppCatalog, powershell_start_apps
    from alfred.automation.service import AppsService

    activity = MemoryActivity()
    apps = AppsService(AppCatalog(powershell_start_apps), ActionLog(activity), None)  # type: ignore[arg-type]
    svc = UiaService(ActionLog(activity))  # type: ignore[arg-type]
    await apps.open_app("calculadora")
    try:
        for auto_id in ("clearButton", "num1Button", "plusButton", "num2Button", "equalButton"):
            await svc.invoke("calculadora", ElementQuery(automation_id=auto_id))
        display = await svc.read_text(
            "calculadora", ElementQuery(automation_id="CalculatorResults")
        )
        assert display.strip().endswith("3"), display
        tree = await svc.describe("calculadora", limit=300)
        assert any(e["id"] == "num7Button" for e in tree)
    finally:
        await svc.stop()
        await apps.close_app("calculadora")
