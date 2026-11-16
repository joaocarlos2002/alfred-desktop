from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from alfred.app import bootstrap
from alfred.core.emergency import EmergencyStop
from alfred.core.event_bus import EventBus
from alfred.core.tasks import TaskRegistry
from alfred.domain.emergency import EmergencyStopped
from alfred.infrastructure.db.activity_repository import SqlActivityLogRepository
from alfred.ui.tray import emergency_message


class FakeReleaser:
    def __init__(self) -> None:
        self.calls = 0

    def release_all(self) -> list[str]:
        self.calls += 1
        return ["ctrl", "mouse_left"]


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


async def test_registry_tracks_and_cancels_tasks() -> None:
    registry = TaskRegistry()
    done = registry.spawn(asyncio.sleep(0), name="rápida")
    await done
    long = [registry.spawn(asyncio.sleep(60), name=f"longa-{i}") for i in range(3)]

    assert registry.running == 3
    assert await registry.cancel_all() == (3, 0)
    assert all(task.cancelled() for task in long)
    assert registry.running == 0
    assert await registry.cancel_all() == (0, 0)


async def test_emergency_cancels_everything_under_500ms(bus: EventBus) -> None:
    registry = TaskRegistry()
    releaser = FakeReleaser()
    emergency = EmergencyStop(registry, bus, input_releaser=releaser)
    await emergency.start()
    received: list[EmergencyStopped] = []

    async def on_stop(event: EmergencyStopped) -> None:
        received.append(event)

    bus.subscribe(EmergencyStopped, on_stop)

    async def automation() -> None:
        steps = 0
        while True:  # simula uma automação executando etapas em laço
            steps += 1
            await asyncio.sleep(0.01)

    tasks = [registry.spawn(automation(), name=f"auto-{i}") for i in range(5)]
    await asyncio.sleep(0.05)

    started = time.perf_counter()
    event = await emergency.trigger("teste")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert all(task.cancelled() for task in tasks)
    assert event.cancelled_tasks == 5
    assert event.stuck_tasks == 0
    assert event.released_inputs == ("ctrl", "mouse_left")
    assert releaser.calls == 1
    await bus.drain(timeout=1)
    assert received == [event]


async def test_task_ignoring_cancellation_is_reported_not_waited_forever(bus: EventBus) -> None:
    registry = TaskRegistry()
    emergency = EmergencyStop(registry, bus, cancel_timeout=0.1)
    await emergency.start()
    release = asyncio.Event()

    async def stubborn() -> None:
        while not release.is_set():
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                continue  # ignora o cancelamento (bug simulado)

    registry.spawn(stubborn(), name="teimosa")
    await asyncio.sleep(0)

    started = time.perf_counter()
    event = await emergency.trigger("teste")

    assert time.perf_counter() - started < 0.5
    assert event.stuck_tasks == 1
    release.set()
    await registry.cancel_all()


async def test_trigger_from_hotkey_thread_bypasses_bus_queue(bus: EventBus) -> None:
    registry = TaskRegistry()
    emergency = EmergencyStop(registry, bus)
    await emergency.start()
    task = registry.spawn(asyncio.sleep(60), name="longa")

    started = time.perf_counter()
    threading.Thread(target=emergency.trigger_threadsafe, args=("hotkey",)).start()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)

    assert time.perf_counter() - started < 0.5
    await emergency.stop()


async def test_repeated_triggers_are_coalesced(bus: EventBus) -> None:
    registry = TaskRegistry()
    releaser = FakeReleaser()
    emergency = EmergencyStop(registry, bus, input_releaser=releaser)
    await emergency.start()
    registry.spawn(asyncio.sleep(60), name="longa")

    for _ in range(5):
        emergency.trigger_threadsafe("hotkey")
    await asyncio.sleep(0.2)
    await emergency.stop()

    assert releaser.calls == 1


async def test_emergency_is_recorded_in_activity_history(tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.emergency is not None
    assert app.tasks is not None
    assert app.database is not None
    await app.manager.start_all()
    try:
        app.tasks.spawn(asyncio.sleep(60), name="longa")

        event = await app.emergency.trigger("teste")

        history = await SqlActivityLogRepository(app.database).list()
        (entry,) = [e for e in history if e.action == "emergency_stop"]
        assert entry.category == "safety"
        assert entry.status == "succeeded"
        assert entry.correlation_id == event.correlation_id
        assert entry.detail["cancelled_tasks"] == 1
    finally:
        await app.manager.stop_all()


def test_emergency_message() -> None:
    message = emergency_message(
        EmergencyStopped(
            trigger="hotkey",
            cancelled_tasks=2,
            stuck_tasks=1,
            released_inputs=("ctrl",),
            elapsed_ms=42.0,
        )
    )
    assert "2 tarefa(s)" in message
    assert "42 ms" in message
    assert "não respondeu" in message
    assert "ctrl" in message


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
def test_win32_releaser_releases_stuck_modifier() -> None:
    import ctypes

    from alfred.infrastructure.input_release import Win32InputReleaser

    releaser = Win32InputReleaser()
    ctypes.windll.user32.keybd_event(0x10, 0, 0, 0)  # shift "preso"
    time.sleep(0.05)
    assert releaser.is_down("shift")

    assert "shift" in releaser.release_all()
    time.sleep(0.05)
    assert not releaser.is_down("shift")
