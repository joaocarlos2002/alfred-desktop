from __future__ import annotations

import asyncio
import sys
import time

import pytest

from alfred.automation.input_service import InputBackend, InputService, UserBusyError
from alfred.core.action_log import ActionLog
from alfred.core.input_lock import InputLock, InputLockTimeoutError
from alfred.domain.activity import ActivityEntry


class MemoryActivity:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        self.entries.append(entry)
        return entry


class Clock:
    """Simula GetLastInputInfo: input do usuário e as próprias injeções do ALFRED."""

    def __init__(self) -> None:
        self.now = 100_000
        self.last_input = 0
        self.user_typing = False

    def idle(self) -> int:
        self.now += 50
        if self.user_typing:
            self.last_input = self.now - 10
        return self.now - self.last_input

    def inject(self) -> None:
        self.last_input = self.now


def make_backend(clock: Clock, log: list[str], delay: float = 0.0) -> InputBackend:
    def typed(text: str) -> None:
        log.append(f"start:{text}")
        time.sleep(delay)
        clock.inject()
        log.append(f"end:{text}")

    def pressed(hotkey: str) -> None:
        log.append(hotkey)
        clock.inject()

    def clicked(x: int, y: int, button: str) -> None:
        log.append(f"click:{x},{y}")
        clock.inject()

    return InputBackend(
        type_text=typed,
        press_hotkey=pressed,
        click=clicked,
        idle_ms=clock.idle,
        now_ms=lambda: clock.now,
    )


def service(
    clock: Clock, log: list[str], delay: float = 0.0
) -> tuple[InputService, MemoryActivity]:
    activity = MemoryActivity()
    svc = InputService(
        InputLock(),
        ActionLog(activity),  # type: ignore[arg-type]
        make_backend(clock, log, delay),
        wait_for_user=0.3,
        poll=0.01,
    )
    return svc, activity


async def test_concurrent_tasks_never_interleave_input() -> None:
    clock, log = Clock(), []
    svc, _ = service(clock, log, delay=0.05)

    await asyncio.gather(svc.type_text("a"), svc.type_text("b"), svc.type_text("c"))

    pairs = [log[i : i + 2] for i in range(0, len(log), 2)]
    assert len(pairs) == 3
    assert all(start.replace("start", "end") == end for start, end in pairs)


async def test_own_injections_do_not_count_as_user_activity() -> None:
    clock, log = Clock(), []
    svc, _ = service(clock, log)
    await svc.type_text("olá")
    await svc.press("enter")  # logo após a própria injeção: não espera o "usuário"
    assert log[-1] == "enter"


async def test_gives_up_while_user_keeps_typing() -> None:
    clock, log = Clock(), []
    svc, activity = service(clock, log)
    clock.user_typing = True

    with pytest.raises(UserBusyError):
        await svc.click(10, 20)

    assert log == []
    assert activity.entries[-1].status == "failed"


async def test_waits_until_user_stops() -> None:
    clock, log = Clock(), []
    svc, _ = service(clock, log)
    clock.user_typing = True

    async def user_stops() -> None:
        await asyncio.sleep(0.05)
        clock.user_typing = False
        clock.last_input = clock.now - 10_000

    await asyncio.gather(svc.press("ctrl+s"), user_stops())
    assert log == ["ctrl+s"]


async def test_cancellation_releases_the_lock() -> None:
    lock = InputLock()
    started = asyncio.Event()

    async def holder() -> None:
        async with lock.hold("automação longa"):
            started.set()
            await asyncio.sleep(10)

    task = asyncio.create_task(holder())
    await started.wait()
    assert lock.owner == "automação longa"
    task.cancel()  # ex.: interrupção de emergência
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked
    async with lock.hold("próxima", timeout=0.1):
        pass


async def test_lock_timeout_names_the_owner() -> None:
    lock = InputLock()
    holding, release = asyncio.Event(), asyncio.Event()

    async def other_task() -> None:
        async with lock.hold("digitar_texto"):
            holding.set()
            await release.wait()

    holder = asyncio.create_task(other_task())
    await holding.wait()
    with pytest.raises(InputLockTimeoutError, match="digitar_texto"):
        async with lock.hold("outra", timeout=0.05):
            pass
    release.set()
    await holder


async def test_lock_is_reentrant_in_the_same_context_only() -> None:
    lock = InputLock()
    async with lock.hold("etapa"), lock.hold("servico interno"):
        assert lock.owner == "etapa"
    assert not lock.locked


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
def test_real_sendinput_key_state() -> None:
    """Injeta Shift via SendInput e confere o estado global (não digita em nenhuma janela)."""
    from alfred.automation import input as win_input
    from alfred.infrastructure.input_release import Win32InputReleaser

    win_input.key_down(0x10)
    time.sleep(0.05)
    assert Win32InputReleaser.is_down("shift")
    win_input.key_up(0x10)
    time.sleep(0.05)
    assert not Win32InputReleaser.is_down("shift")
    assert win_input.idle_ms() < 1000  # a injeção conta como input do sistema
