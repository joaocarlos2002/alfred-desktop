from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from alfred.automation.apps import (
    AmbiguousAppError,
    AppCatalog,
    AppNotFoundError,
    StartApp,
)
from alfred.automation.service import AppsService
from alfred.automation.windows import WindowInfo
from alfred.core.action_log import ActionLog
from alfred.core.commands import CommandRegistry
from alfred.core.event_bus import EventBus
from alfred.core.voice_commands import VoiceCommandHandler
from alfred.domain.activity import ActivityEntry
from alfred.domain.confirmation import ConfirmationDecision

APPS = [
    StartApp("Calculadora", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"),
    StartApp("Bloco de notas", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
    StartApp("Microsoft Edge", "MSEdge"),
    StartApp("Microsoft Teams", "MSTeams"),
    StartApp("calculadora", "duplicado"),
]


def catalog(aliases: dict[str, list[str]] | None = None) -> AppCatalog:
    return AppCatalog(lambda: list(APPS), aliases)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("calculadora", "Calculadora"),
        ("Calculadora", "Calculadora"),
        ("bloco de notas", "Bloco de notas"),
        ("edge", "Microsoft Edge"),
        ("calculador", "Calculadora"),
    ],
)
def test_resolves_by_name(query: str, expected: str) -> None:
    assert catalog().resolve(query).name == expected


def test_aliases_and_duplicates() -> None:
    cat = catalog({"Calculadora": ["calc"]})
    assert cat.resolve("calc").name == "Calculadora"
    assert len([a for a in cat.apps if a.name.casefold() == "calculadora"]) == 1


def test_ambiguous_and_not_found() -> None:
    with pytest.raises(AmbiguousAppError) as exc:
        catalog().resolve("microsoft")
    assert {c.name for c in exc.value.candidates} >= {"Microsoft Edge", "Microsoft Teams"}
    with pytest.raises(AppNotFoundError):
        catalog().resolve("photoshop")


class MemoryActivity:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        self.entries.append(entry)
        return entry

    async def list(self, **kwargs: Any) -> list[ActivityEntry]:
        return list(reversed(self.entries))


class FakeDesktop:
    def __init__(self) -> None:
        self.windows: list[WindowInfo] = [WindowInfo(1, "Documento - Word", 10, "WINWORD.EXE")]
        self.launched: list[str] = []
        self.closed: list[int] = []
        self.stubborn: set[int] = set()
        self.killed: list[int] = []

    def launch(self, app: StartApp) -> None:
        self.launched.append(app.name)
        self.windows.append(WindowInfo(99, app.name, 500, "CalculatorApp.exe"))

    def list(self) -> list[WindowInfo]:
        return list(self.windows)

    def close(self, hwnd: int) -> None:
        self.closed.append(hwnd)
        if hwnd not in self.stubborn:
            self.windows = [w for w in self.windows if w.hwnd != hwnd]

    def wait(self, hwnds: list[int], timeout: float) -> list[int]:
        alive = {w.hwnd for w in self.windows}
        return [h for h in hwnds if h in alive]

    def kill(self, pids: list[int]) -> None:
        self.killed.extend(pids)
        self.windows = [w for w in self.windows if w.pid not in pids]


class FakeConfirmations:
    def __init__(self, decision: ConfirmationDecision) -> None:
        self.decision = decision
        self.requests: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> ConfirmationDecision:
        self.requests.append(kwargs)
        return self.decision


def service(
    desktop: FakeDesktop,
    decision: ConfirmationDecision = ConfirmationDecision.DENIED,
) -> tuple[AppsService, MemoryActivity, FakeConfirmations]:
    activity = MemoryActivity()
    confirmations = FakeConfirmations(decision)
    svc = AppsService(
        catalog(),
        ActionLog(activity),  # type: ignore[arg-type]
        confirmations,  # type: ignore[arg-type]
        launcher=desktop.launch,
        windows=desktop.list,
        closer=desktop.close,
        killer=desktop.kill,
        waiter=desktop.wait,
        open_timeout=0.5,
    )
    return svc, activity, confirmations


async def test_open_launches_and_waits_for_window() -> None:
    desktop = FakeDesktop()
    svc, activity, _ = service(desktop)

    result = await svc.open_app("calculadora")

    assert desktop.launched == ["Calculadora"]
    assert result == {"app": "Calculadora", "window": "Calculadora"}
    assert activity.entries[0].action == "open_app"
    assert activity.entries[0].status == "succeeded"


async def test_close_is_graceful_and_logged() -> None:
    desktop = FakeDesktop()
    desktop.launch(APPS[0])
    svc, activity, confirmations = service(desktop)

    result = await svc.close_app("calculadora")

    assert result == {"app": "Calculadora", "closed": 1, "forced": False}
    assert desktop.closed == [99]
    assert desktop.killed == []
    assert confirmations.requests == []
    assert activity.entries[-1].status == "succeeded"


@pytest.mark.parametrize(
    ("decision", "killed", "status"),
    [
        (ConfirmationDecision.APPROVED, [500], "succeeded"),
        (ConfirmationDecision.DENIED, [], "denied"),
    ],
)
async def test_force_close_requires_confirmation(
    decision: ConfirmationDecision, killed: list[int], status: str
) -> None:
    desktop = FakeDesktop()
    desktop.launch(APPS[0])
    desktop.stubborn.add(99)  # janela não fecha (ex.: diálogo "salvar?")
    svc, activity, confirmations = service(desktop, decision)

    await svc.close_app("calculadora")

    assert confirmations.requests[0]["action"] == "Forçar o encerramento de Calculadora"
    assert desktop.killed == killed
    assert activity.entries[-1].status == status


async def test_close_when_not_open() -> None:
    svc, _, _ = service(FakeDesktop())
    assert (await svc.close_app("calculadora"))["closed"] == 0


async def test_voice_commands_open_and_close_apps() -> None:
    desktop = FakeDesktop()
    svc, _, _ = service(desktop)
    registry = CommandRegistry()
    svc.register_commands(registry)
    handler = VoiceCommandHandler(EventBus(), emergency=_noop, registry=registry)

    opened = await handler.interpret("Alfred, abra a calculadora.")
    ambiguous = await handler.interpret("abrir o microsoft")
    missing = await handler.interpret("abra o photoshop")
    closed = await handler.interpret("feche a calculadora")

    assert opened.reply == "Abrindo Calculadora."
    assert opened.understood
    assert ambiguous.reply is not None
    assert ambiguous.reply.startswith("Qual deles?")
    assert not missing.understood
    assert closed.reply == "Calculadora fechado."


async def _noop() -> None:
    return None


async def test_aliases_are_persisted(tmp_path: Path) -> None:
    from alfred.infrastructure.db.application_repository import SqlApplicationRepository
    from alfred.infrastructure.db.database import Database

    db = Database(tmp_path / "alfred.db")
    await db.start()
    repo = SqlApplicationRepository(db)
    svc = AppsService(catalog(), ActionLog(MemoryActivity()), None, aliases=repo)  # type: ignore[arg-type]
    await svc.start()
    await svc.add_alias("Calculadora", "contas")
    await svc.add_alias("Calculadora", "contas")

    assert await repo.list_aliases() == {"Calculadora": ["contas"]}
    assert svc.catalog.resolve("contas").name == "Calculadora"
    await db.stop()


# ------------------------------------------------------------------ Windows real
@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
async def test_real_open_and_close_calculator() -> None:
    from alfred.automation.apps import powershell_start_apps
    from alfred.automation.windows import list_windows

    apps = powershell_start_apps()
    assert any(a.name == "Calculadora" for a in apps)
    activity = MemoryActivity()
    svc = AppsService(AppCatalog(lambda: apps), ActionLog(activity), None)  # type: ignore[arg-type]

    opened = await svc.open_app("calculadora")
    assert opened["window"] is not None, "a janela da Calculadora não apareceu"
    closed = await svc.close_app("calculadora")

    assert closed["closed"] >= 1
    assert closed["forced"] is False
    assert not [w for w in list_windows() if w.title == "Calculadora"]
    assert [e.status for e in activity.entries] == ["succeeded", "succeeded"]
