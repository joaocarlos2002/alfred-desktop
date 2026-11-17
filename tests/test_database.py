from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alfred.domain.activity import ActivityEntry, ActivityStatus
from alfred.infrastructure.db.activity_repository import SqlActivityLogRepository
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.models import Base, Conversation, Message, Task, TaskStep

EXPECTED_TABLES = {
    "ai_providers",
    "conversations",
    "messages",
    "tasks",
    "task_steps",
    "applications",
    "triggers",
    "trigger_executions",
    "activity_logs",
    "permissions",
    "memories",
}


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "alfred.db")
    await database.start()
    yield database
    await database.stop()


def _sync_engine(path: Path):  # type: ignore[no-untyped-def]
    return create_engine(f"sqlite:///{path.as_posix()}")


async def test_fresh_database_is_migrated_to_head(db: Database) -> None:
    engine = _sync_engine(db.path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables >= EXPECTED_TABLES
        assert "settings" not in tables  # ADR-0004
        assert db.current_revision() == "0001"
    finally:
        engine.dispose()


async def test_migrations_match_models(db: Database) -> None:
    engine = _sync_engine(db.path)
    try:
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()


async def test_wal_and_foreign_keys_are_enabled(db: Database) -> None:
    def pragmas(session: Session) -> tuple[str, int]:
        conn = session.connection()
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        fks = conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        return str(mode), int(fks)

    assert await db.run(pragmas) == ("wal", 1)


async def test_restart_does_not_remigrate_or_backup(tmp_path: Path) -> None:
    path = tmp_path / "alfred.db"
    for _ in range(2):
        database = Database(path)
        await database.start()
        await database.stop()

    assert list(tmp_path.glob("*.bak")) == []


async def test_outdated_database_is_backed_up_before_migration(tmp_path: Path) -> None:
    path = tmp_path / "alfred.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legado (x INTEGER)")
        conn.execute("INSERT INTO legado VALUES (42)")

    database = Database(path)
    await database.start()
    await database.stop()

    (backup,) = tmp_path.glob("alfred.db.pre-0001-*.bak")
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT x FROM legado").fetchone() == (42,)


async def test_transaction_rolls_back_on_error(db: Database) -> None:
    def fail(session: Session) -> None:
        session.add(Conversation(id="c1", title="rascunho"))
        session.flush()
        raise RuntimeError("abortar")

    with pytest.raises(RuntimeError):
        await db.run(fail)

    count = await db.run(lambda s: len(s.scalars(select(Conversation)).all()))
    assert count == 0


async def test_cascade_and_timezone_aware_timestamps(db: Database) -> None:
    def create(session: Session) -> None:
        conversation = Conversation(id="c1", title="teste")
        conversation.messages.append(Message(role="user", content="olá"))
        session.add(conversation)

    await db.run(create)

    created = await db.run(lambda s: s.scalars(select(Message)).one().created_at)
    assert created.tzinfo is UTC

    await db.run(lambda s: s.delete(s.get(Conversation, "c1")))
    assert await db.run(lambda s: s.scalars(select(Message)).all()) == []


async def test_task_step_positions_are_unique(db: Database) -> None:
    def create(session: Session) -> None:
        task = Task(id="t1", correlation_id=str(uuid4()), title="t", status="pending", source="ui")
        task.steps.extend(
            [
                TaskStep(position=1, tool="open_app", status="pending"),
                TaskStep(position=1, tool="type_text", status="pending"),
            ]
        )
        session.add(task)

    with pytest.raises(IntegrityError):
        await db.run(create)


async def test_naive_datetimes_are_rejected(db: Database) -> None:
    repo = SqlActivityLogRepository(db)
    entry = ActivityEntry(
        timestamp=datetime(2026, 1, 1),
        category="automation",
        action="click",
        status=ActivityStatus.SUCCEEDED,
    )
    with pytest.raises(Exception, match="fuso"):
        await repo.add(entry)


async def test_activity_repository_roundtrip_and_deletion(db: Database) -> None:
    repo = SqlActivityLogRepository(db)
    cid = uuid4()
    now = datetime.now(UTC)
    old = await repo.add(
        ActivityEntry(
            timestamp=now - timedelta(days=40),
            category="automation",
            action="open_app",
            status=ActivityStatus.SUCCEEDED,
        )
    )
    recent = await repo.add(
        ActivityEntry(
            correlation_id=cid,
            category="automation",
            action="type_text",
            status=ActivityStatus.FAILED,
            detail={"error": "janela não encontrada"},
        )
    )

    assert [e.id for e in await repo.list()] == [recent.id, old.id]
    (only,) = await repo.list(correlation_id=cid)
    assert only.detail == {"error": "janela não encontrada"}
    assert only.correlation_id == cid
    assert only.timestamp.tzinfo is UTC

    assert await repo.delete_older_than(now - timedelta(days=30)) == 1
    assert old.id is not None
    assert await repo.delete(old.id) is False
    assert recent.id is not None
    assert await repo.delete(recent.id) is True
    assert await repo.list() == []


async def test_run_requires_started_database(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="não iniciado"):
        await Database(tmp_path / "x.db").run(lambda s: None)


async def test_slow_close_is_diagnosed_and_does_not_block_the_loop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressão: o encerramento do banco chegou a passar de 5 s em uso real."""
    import asyncio
    import threading
    import time

    from alfred.infrastructure.db import database as module

    monkeypatch.setattr(module, "SLOW_CLOSE_SECONDS", 0.1)
    db = Database(tmp_path / "alfred.db")
    await db.start()
    release = threading.Event()

    def slow_write(session: Session) -> None:
        release.wait(5)

    pending = asyncio.ensure_future(db.run(slow_write))  # ocupa a thread do banco
    await asyncio.sleep(0.05)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while not release.is_set():
            ticks += 1
            await asyncio.sleep(0.02)

    heartbeat = asyncio.ensure_future(ticker())
    threading.Timer(0.4, release.set).start()
    started = time.perf_counter()
    with caplog.at_level("WARNING"):
        await db.stop()
    await pending
    await heartbeat
    assert time.perf_counter() - started < 3
    assert ticks >= 5  # o event loop continuou rodando durante o encerramento
    warning = next(
        r.getMessage() for r in caplog.records if "demorando para fechar" in r.getMessage()
    )
    assert "slow_write" in warning  # diz qual operação segurou o banco
