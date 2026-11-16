from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from alfred.automation.browser import BrowserService, SubmissionDeniedError
from alfred.core.action_log import ActionLog
from alfred.domain.activity import ActivityEntry
from alfred.domain.confirmation import ConfirmationDecision

FORM = """<!doctype html><html><head><title>Formulário de teste</title></head><body>
<h1 id="titulo">Cadastro</h1>
<input id="nome"><button id="saudar" onclick="document.getElementById('saida').textContent =
'Olá, ' + document.getElementById('nome').value + '!'">Saudar</button>
<p id="saida"></p>
<form onsubmit="document.getElementById('saida').textContent='enviado'; return false;">
<button id="enviar" type="submit">Enviar</button></form></body></html>"""


class MemoryActivity:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        self.entries.append(entry)
        return entry


class FakeConfirmations:
    def __init__(self, decision: ConfirmationDecision) -> None:
        self.decision = decision
        self.requests: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> ConfirmationDecision:
        self.requests.append(kwargs)
        return self.decision


def test_profile_is_dedicated_and_browser_starts_on_demand(tmp_path: Path) -> None:
    svc = BrowserService(ActionLog(MemoryActivity()), None, tmp_path / "browser-profile")  # type: ignore[arg-type]
    assert not svc.running
    assert svc.profile_dir == tmp_path / "browser-profile"


@pytest.mark.skipif(sys.platform != "win32", reason="Edge instalado")
@pytest.mark.windows
async def test_real_edge_fill_click_extract_and_confirmed_submit(tmp_path: Path) -> None:
    page = tmp_path / "form.html"
    page.write_text(FORM, encoding="utf-8")
    activity = MemoryActivity()
    confirmations = FakeConfirmations(ConfirmationDecision.DENIED)
    svc = BrowserService(
        ActionLog(activity),  # type: ignore[arg-type]
        confirmations,  # type: ignore[arg-type]
        tmp_path / "browser-profile",
        headless=True,
        idle_close_seconds=0.2,
    )
    try:
        nav = await svc.navigate(page.as_uri())
        assert nav["titulo"] == "Formulário de teste"
        await svc.fill("#nome", "Alfred")
        await svc.click("#saudar")
        assert await svc.extract_text("#saida") == "Olá, Alfred!"
        assert len(await svc.screenshot()) > 1000

        with pytest.raises(SubmissionDeniedError):
            await svc.submit("#enviar", description="formulário de cadastro")
        assert await svc.extract_text("#saida") == "Olá, Alfred!"  # nada foi enviado

        confirmations.decision = ConfirmationDecision.APPROVED
        await svc.submit("#enviar", description="formulário de cadastro")
        assert await svc.extract_text("#saida") == "enviado"
    finally:
        await svc.close_browser()

    actions = [(e.action, e.status) for e in activity.entries]
    assert ("enviar_formulario", "denied") in actions
    assert ("enviar_formulario", "succeeded") in actions
    assert (tmp_path / "browser-profile").is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Edge instalado")
@pytest.mark.windows
async def test_real_browser_closes_when_idle(tmp_path: Path) -> None:
    import asyncio

    page = tmp_path / "p.html"
    page.write_text("<title>t</title><p>x</p>", encoding="utf-8")
    svc = BrowserService(
        ActionLog(MemoryActivity()),
        None,
        tmp_path / "profile",  # type: ignore[arg-type]
        headless=True,
        idle_close_seconds=0.3,
    )
    await svc.navigate(page.as_uri())
    assert svc.running
    for _ in range(50):
        await asyncio.sleep(0.1)
        if not svc.running:
            break
    assert not svc.running
