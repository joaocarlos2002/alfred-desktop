from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from alfred.core.event_bus import EventBus
from alfred.domain.events import Event
from alfred.domain.tasks import TaskFinished, TaskStatus
from alfred.domain.triggers import TriggerBlocked, TriggerSpec
from alfred.infrastructure.db.database import Database
from alfred.infrastructure.db.trigger_repository import SqlTriggerRepository
from alfred.triggers.engine import TriggerEngine
from alfred.triggers.guard import GuardLimits, TriggerGuard
from alfred.ui.tray import trigger_blocked_message


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeHotkeys:
    def __init__(self) -> None:
        self.by_combo: dict[str, Callable[[], None]] = {}

    async def register_extra(self, hotkey: str, callback: Callable[[], None]) -> int:
        self.by_combo[hotkey] = callback
        return len(self.by_combo)

    async def unregister_extra(self, hotkey_id: int) -> None:
        return None


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "alfred.db")
    await db.start()
    yield db
    await db.stop()


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


async def collect(bus: EventBus) -> list[Event]:
    seen: list[Event] = []

    async def record(event: Event) -> None:
        seen.append(event)

    bus.subscribe(Event, record)
    return seen


def hotkey(name: str, combo: str, request: str) -> TriggerSpec:
    return TriggerSpec(name=name, kind="hotkey", config={"atalho": combo}, request=request)


async def settle(bus: EventBus) -> None:
    for _ in range(5):
        await asyncio.sleep(0.02)
        await bus.drain(timeout=1)


async def test_artificial_cycle_is_detected_blocked_recorded_and_notified(
    database: Database, bus: EventBus
) -> None:
    """A aperta o atalho de B e B aperta o de A: sem proteção, isso nunca pararia."""
    hotkeys = FakeHotkeys()
    submitted: list[str] = []
    pending: set[asyncio.Task[None]] = set()
    engine: TriggerEngine

    def submit(request: str, task_id: UUID) -> None:
        submitted.append(request)

        async def task() -> None:  # a tarefa do ALFRED pressiona o outro atalho e termina
            hotkeys.by_combo[request.removeprefix("aperte ")]()
            await asyncio.sleep(0.01)
            await bus.publish(TaskFinished(task_id=task_id, status=TaskStatus.SUCCEEDED))

        job = asyncio.ensure_future(task())
        pending.add(job)
        job.add_done_callback(pending.discard)

    engine = TriggerEngine(
        SqlTriggerRepository(database), bus, submit, hotkeys=hotkeys, processes=None, power=None
    )
    events = await collect(bus)
    await engine.start()
    a = await engine.save(hotkey("A", "ctrl+alt+1", "aperte ctrl+alt+2"))
    b = await engine.save(hotkey("B", "ctrl+alt+2", "aperte ctrl+alt+1"))
    hotkeys.by_combo["ctrl+alt+1"]()  # o usuário aperta A uma vez
    await settle(bus)

    assert submitted == ["aperte ctrl+alt+2", "aperte ctrl+alt+1"]  # A e B, e então para
    [blocked] = [e for e in events if isinstance(e, TriggerBlocked)]
    assert blocked.trigger_id == a.id
    assert "ciclo de gatilhos detectado: A → B → A" in blocked.reason
    records = {r.trigger_id: r for r in await engine.executions() if r.status == "blocked"}
    assert records[a.id or 0].error == blocked.reason  # registrado no histórico
    assert b.id not in records
    assert "não foi executado" in trigger_blocked_message(blocked)  # aviso da bandeja
    await engine.stop()


def test_chain_depth_limit() -> None:
    guard = TriggerGuard(GuardLimits(max_chain_depth=2), clock=Clock())
    ids = [UUID(int=i) for i in range(4)]
    first = guard.check(1, "hotkey")
    guard.started(ids[0], 1, first.chain)
    second = guard.check(2, "app_open")
    assert second.chain == (1,)
    guard.started(ids[1], 2, second.chain)
    third = guard.check(3, "system")
    assert not third.allowed
    assert "longa demais" in (third.reason or "")


def test_root_sources_start_a_new_chain() -> None:
    guard = TriggerGuard(clock=Clock())
    guard.started(UUID(int=1), 1, ())
    assert guard.check(2, "hotkey").chain == (1,)
    for kind in ("voice", "schedule", "startup"):
        assert guard.check(1, kind).allowed  # não são causados pela tarefa de 1


def test_causality_expires_after_the_window() -> None:
    clock = Clock()
    guard = TriggerGuard(GuardLimits(causal_window_seconds=10), clock=clock)
    guard.started(UUID(int=1), 1, ())
    guard.finished(UUID(int=1), failed=False)
    clock.now += 5
    assert not guard.check(1, "app_open").allowed  # 1 → 1, ainda é causa
    clock.now += 6
    assert guard.check(1, "app_open").allowed


def test_rate_limit_per_trigger() -> None:
    clock = Clock()
    guard = TriggerGuard(GuardLimits(max_per_window=3, window_seconds=60), clock=clock)
    for i in range(3):
        verdict = guard.check(7, "schedule")
        assert verdict.allowed
        guard.started(UUID(int=i), 7, ())
    blocked = guard.check(7, "schedule")
    assert not blocked.allowed
    assert "3 disparos" in (blocked.reason or "")
    assert guard.check(8, "schedule").allowed  # o limite é por gatilho
    clock.now += 61
    assert guard.check(7, "schedule").allowed


def test_circuit_breaker_opens_after_consecutive_failures_and_recovers() -> None:
    clock = Clock()
    guard = TriggerGuard(
        GuardLimits(breaker_failures=2, breaker_cooldown_seconds=300, max_per_window=99),
        clock=clock,
    )
    guard.started(UUID(int=1), 5, ())
    assert not guard.finished(UUID(int=1), failed=True)
    guard.started(UUID(int=2), 5, ())
    assert not guard.finished(UUID(int=2), failed=False)  # sucesso zera a contagem
    for i in (3, 4):
        guard.started(UUID(int=i), 5, ())
    assert not guard.finished(UUID(int=3), failed=True)
    assert guard.finished(UUID(int=4), failed=True)  # abriu agora
    verdict = guard.check(5, "voice")
    assert not verdict.allowed
    assert "falhas seguidas" in (verdict.reason or "")
    clock.now += 301
    assert guard.check(5, "voice").allowed


async def test_breaker_notifies_and_survives_restart(database: Database, bus: EventBus) -> None:
    repo = SqlTriggerRepository(database)
    submitted: list[tuple[str, UUID]] = []

    def submit(request: str, task_id: UUID) -> None:
        submitted.append((request, task_id))

    limits = GuardLimits(breaker_failures=2, max_per_window=99)
    events = await collect(bus)
    engine = TriggerEngine(
        repo, bus, submit, processes=None, power=None, guard=TriggerGuard(limits)
    )
    await engine.start()
    spec = await engine.save(
        TriggerSpec(name="Relatório", kind="voice", config={"frase": "r"}, request="gere")
    )
    for _ in range(2):
        assert await engine.fire(spec)
        await bus.publish(
            TaskFinished(task_id=submitted[-1][1], status=TaskStatus.FAILED, error="x")
        )
        await bus.drain(timeout=1)
    assert not await engine.fire(spec)
    await bus.drain(timeout=1)
    reasons = [e.reason for e in events if isinstance(e, TriggerBlocked)]
    assert len(reasons) == 2  # ao abrir o breaker e ao barrar o disparo seguinte
    assert all("falhas seguidas" in r for r in reasons)
    await engine.stop()

    again = TriggerEngine(repo, bus, submit, processes=None, power=None, guard=TriggerGuard(limits))
    await again.start()
    assert again.guard.is_open(spec.id or 0)  # lido do histórico de execuções
    await again.stop()


def _unused(*_: Any) -> None:
    return None
