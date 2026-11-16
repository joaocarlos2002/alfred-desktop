from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from alfred.core.action_log import ActionLog
from alfred.domain.events import correlation_scope
from alfred.infrastructure.db.activity_repository import SqlActivityLogRepository
from alfred.infrastructure.db.database import Database


@pytest.fixture
async def log(tmp_path: Path) -> AsyncIterator[tuple[ActionLog, SqlActivityLogRepository]]:
    db = Database(tmp_path / "alfred.db")
    await db.start()
    repo = SqlActivityLogRepository(db)
    yield ActionLog(repo), repo
    await db.stop()


async def test_success_is_recorded_with_duration_result_and_correlation(
    log: tuple[ActionLog, SqlActivityLogRepository],
) -> None:
    action_log, repo = log
    cid = uuid4()
    with correlation_scope(cid):
        async with action_log.record("automation", "open_app", params={"app": "Calc"}) as rec:
            await asyncio.sleep(0.05)
            rec.result = {"pid": 42}

    (entry,) = await repo.list()
    assert entry.status == "succeeded"
    assert entry.correlation_id == cid
    assert entry.detail["params"] == {"app": "Calc"}
    assert entry.detail["result"] == {"pid": 42}
    # Margem folgada: a resolução do timer no Windows é de ~15 ms.
    assert entry.detail["duration_ms"] >= 25


async def test_failure_cancellation_and_denial(
    log: tuple[ActionLog, SqlActivityLogRepository],
) -> None:
    action_log, repo = log
    with pytest.raises(RuntimeError):
        async with action_log.record("automation", "click"):
            raise RuntimeError("elemento não encontrado")

    async def long_action() -> None:
        async with action_log.record("automation", "type_text"):
            await asyncio.sleep(10)

    task = asyncio.create_task(long_action())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with action_log.record("automation", "close_app") as rec:
        rec.deny("confirmação recusada")

    by_action = {e.action: e for e in await repo.list()}
    assert by_action["click"].status == "failed"
    assert "elemento não encontrado" in by_action["click"].detail["error"]
    assert by_action["type_text"].status == "cancelled"
    assert by_action["close_app"].status == "denied"


async def test_sensitive_data_is_masked(log: tuple[ActionLog, SqlActivityLogRepository]) -> None:
    action_log, repo = log
    async with action_log.record(
        "automation", "fill_form", params={"user": "joao", "password": "hunter2"}
    ) as rec:
        rec.result = {"token": "abc"}

    (entry,) = await repo.list()
    raw = json.dumps(entry.detail)
    assert "hunter2" not in raw
    assert "abc" not in raw
    assert entry.detail["params"]["user"] == "joao"


async def test_export_writes_masked_json_lines(
    log: tuple[ActionLog, SqlActivityLogRepository], tmp_path: Path
) -> None:
    action_log, _ = log
    for name in ("open_app", "close_app"):
        async with action_log.record("automation", name, params={"senha": "x"}):
            pass

    path = await action_log.export(tmp_path / "exports")

    lines = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert [line["action"] for line in lines] == ["open_app", "close_app"]  # cronológico
    assert lines[0]["detail"]["params"]["senha"] == "***"
    assert path.name.startswith("atividades-")
