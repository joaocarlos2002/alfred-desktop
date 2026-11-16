from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from alfred.core.action_log import ActionLog
from alfred.core.permissions import ALWAYS_CONFIRM, PermissionService, Policy
from alfred.core.tools import Risk, Tool
from alfred.domain.activity import ActivityEntry
from alfred.domain.confirmation import ConfirmationDecision


class NoParams(BaseModel):
    pass


async def _noop(p: NoParams) -> dict[str, Any]:
    return {}


def tool(name: str, category: str, risk: Risk = Risk.ACTION) -> Tool:
    return Tool(name, f"descrição de {name}", NoParams, _noop, category, risk=risk)


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str | None], str] = {}

    async def policies(self) -> dict[tuple[str, str | None], str]:
        return dict(self.data)

    async def set_policy(self, category: str, tool: str | None, policy: str) -> None:
        self.data[(category, tool)] = policy


class MemoryActivity:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        self.entries.append(entry)
        return entry


class FakeConfirmations:
    def __init__(self, decision: ConfirmationDecision = ConfirmationDecision.APPROVED) -> None:
        self.decision = decision
        self.requests: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> ConfirmationDecision:
        self.requests.append(kwargs)
        return self.decision


async def service(
    decision: ConfirmationDecision = ConfirmationDecision.APPROVED,
) -> tuple[PermissionService, FakeConfirmations, MemoryActivity, MemoryStore]:
    store, confirmations, activity = MemoryStore(), FakeConfirmations(decision), MemoryActivity()
    svc = PermissionService(store, confirmations, ActionLog(activity))  # type: ignore[arg-type]
    await svc.start()
    return svc, confirmations, activity, store


async def test_defaults_by_risk() -> None:
    svc, confirmations, _, _ = await service()
    assert await svc.authorize(tool("abrir_aplicativo", "applications"), "Abrir app")
    assert await svc.authorize(tool("ler_elemento", "ui", Risk.READ), "Ler")
    assert confirmations.requests == []
    assert svc.policy_for(tool("x", "outra", Risk.SENSITIVE)) is Policy.ASK


@pytest.mark.parametrize("category", sorted(ALWAYS_CONFIRM))
async def test_sensitive_categories_always_ask_even_if_allowed(category: str) -> None:
    svc, confirmations, _, _ = await service()
    await svc.set_policy(category, Policy.ALLOW)
    sensitive = tool("acao", category)
    assert svc.policy_for(sensitive) is Policy.ASK
    assert await svc.authorize(sensitive, "Fazer algo sensível", "alvo")
    assert len(confirmations.requests) == 1
    assert confirmations.requests[0]["category"] == category


async def test_denied_confirmation_and_deny_policy_are_logged() -> None:
    svc, _, activity, _ = await service(ConfirmationDecision.TIMEOUT)
    assert not await svc.authorize(tool("apagar_arquivo", "files"), "Excluir relatório.docx")
    await svc.set_policy("input", Policy.DENY)
    assert not await svc.authorize(tool("digitar_texto", "input"), "Digitar")
    assert [e.status for e in activity.entries] == ["denied", "denied"]


async def test_tool_override_beats_category_and_persists() -> None:
    svc, _, _, store = await service()
    await svc.set_policy("applications", Policy.DENY)
    await svc.set_policy("applications", Policy.ALLOW, tool="abrir_aplicativo")
    assert svc.policy_for(tool("abrir_aplicativo", "applications")) is Policy.ALLOW
    assert svc.policy_for(tool("fechar_aplicativo", "applications")) is Policy.DENY

    again = PermissionService(store, None, ActionLog(MemoryActivity()))  # type: ignore[arg-type]
    await again.start()
    assert again.policy_for(tool("fechar_aplicativo", "applications")) is Policy.DENY


async def test_without_confirmation_ui_sensitive_actions_are_denied() -> None:
    svc = PermissionService(MemoryStore(), None, ActionLog(MemoryActivity()))  # type: ignore[arg-type]
    await svc.start()
    assert not await svc.authorize(tool("comprar", "purchases"), "Comprar")


async def test_sqlite_repository(tmp_path: Path) -> None:
    from alfred.infrastructure.db.database import Database
    from alfred.infrastructure.db.permission_repository import SqlPermissionRepository

    db = Database(tmp_path / "alfred.db")
    await db.start()
    repo = SqlPermissionRepository(db)
    await repo.set_policy("files", None, "ask")
    await repo.set_policy("files", None, "deny")
    await repo.set_policy("applications", "abrir_aplicativo", "allow")
    assert await repo.policies() == {
        ("files", None): "deny",
        ("applications", "abrir_aplicativo"): "allow",
    }
    await db.stop()
