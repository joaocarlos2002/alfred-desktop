from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.hotkey_service import HotkeyService
from alfred.core.settings_service import SettingsService
from alfred.domain.hotkeys import (
    HotkeyAction,
    HotkeyConflictError,
    HotkeyPressed,
    HotkeyRegistrationFailed,
)
from alfred.infrastructure.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey

# Combinação improvável de colidir com qualquer programa real.
TEST_HOTKEY = "ctrl+alt+shift+f24"


@pytest.mark.parametrize(
    ("hotkey", "expected"),
    [
        ("ctrl+alt+space", (MOD_CONTROL | MOD_ALT, 0x20)),
        ("ctrl+alt+shift+escape", (MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x1B)),
        ("alt+k", (MOD_ALT, ord("K"))),
        ("ctrl+f12", (MOD_CONTROL, 0x7B)),
        ("shift+7", (MOD_SHIFT, ord("7"))),
    ],
)
def test_parse_hotkey(hotkey: str, expected: tuple[int, int]) -> None:
    assert parse_hotkey(hotkey) == expected


@pytest.mark.parametrize("hotkey", ["space", "hyper+a", "ctrl+banana", "ctrl+ç"])
def test_parse_hotkey_rejects_invalid(hotkey: str) -> None:
    with pytest.raises(ValueError, match=r"atalho|modificador|tecla"):
        parse_hotkey(hotkey)


# ------------------------------------------------------------------ Win32 real
win32_only = pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey é Win32")


@pytest.fixture
def listener() -> Iterator[object]:
    from alfred.infrastructure.hotkeys import Win32HotkeyListener

    instance = Win32HotkeyListener()
    instance.start()
    yield instance
    instance.stop()


@win32_only
def test_win32_register_conflict_and_unregister(listener: object) -> None:
    from alfred.infrastructure.hotkeys import Win32HotkeyListener

    assert isinstance(listener, Win32HotkeyListener)
    hotkey_id = listener.register(TEST_HOTKEY, lambda: None)

    with pytest.raises(HotkeyConflictError):
        listener.register(TEST_HOTKEY, lambda: None)

    listener.unregister(hotkey_id)
    listener.unregister(listener.register(TEST_HOTKEY, lambda: None))


@win32_only
def test_win32_listener_requires_start() -> None:
    from alfred.infrastructure.hotkeys import Win32HotkeyListener

    with pytest.raises(RuntimeError, match="não iniciado"):
        Win32HotkeyListener().register(TEST_HOTKEY, lambda: None)


@win32_only
@pytest.mark.windows
def test_win32_hotkey_fires_with_other_app_focused(listener: object) -> None:
    """Simula a combinação com SendInput: o atalho dispara independente do foco."""
    import ctypes

    from alfred.infrastructure.hotkeys import Win32HotkeyListener

    assert isinstance(listener, Win32HotkeyListener)
    fired = threading.Event()
    listener.register(TEST_HOTKEY, fired.set)

    user32 = ctypes.windll.user32
    keys = [0x11, 0x12, 0x10, 0x87]  # ctrl, alt, shift, F24
    for vk in keys:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(keys):
        user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP

    assert fired.wait(timeout=2)


# ------------------------------------------------------------------ serviço
class FakeBackend:
    def __init__(self, taken: set[str] | None = None) -> None:
        self.taken = taken or set()
        self.active: dict[int, tuple[str, Callable[[], None]]] = {}
        self._next = 1
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def register(self, hotkey: str, callback: Callable[[], None]) -> int:
        if hotkey in self.taken or any(h == hotkey for h, _ in self.active.values()):
            raise HotkeyConflictError(f"'{hotkey}' já está em uso")
        hotkey_id, self._next = self._next, self._next + 1
        self.active[hotkey_id] = (hotkey, callback)
        return hotkey_id

    def unregister(self, hotkey_id: int) -> None:
        self.active.pop(hotkey_id, None)

    def press(self, hotkey: str) -> None:
        for combo, callback in self.active.values():
            if combo == hotkey:
                threading.Thread(target=callback).start()  # como a thread Win32


async def make_service(
    tmp_path: Path, backend: FakeBackend
) -> tuple[HotkeyService, SettingsService, EventBus, list[object]]:
    bus = EventBus()
    await bus.start()
    seen: list[object] = []

    async def record(event: object) -> None:
        seen.append(event)

    bus.subscribe(HotkeyPressed, record)
    bus.subscribe(HotkeyRegistrationFailed, record)
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    service = HotkeyService(backend, settings, bus)
    await service.start()
    return service, settings, bus, seen


async def wait_for(predicate: Callable[[], bool], bus: EventBus) -> None:
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        await bus.drain(timeout=1)
    assert predicate()


async def test_registers_configured_hotkeys_and_publishes_presses(tmp_path: Path) -> None:
    backend = FakeBackend()
    service, _, bus, seen = await make_service(tmp_path, backend)

    assert service.registered == {
        HotkeyAction.ACTIVATE: "ctrl+alt+space",
        HotkeyAction.EMERGENCY: "ctrl+alt+shift+escape",
    }

    backend.press("ctrl+alt+space")
    await wait_for(lambda: len(seen) == 1, bus)
    event = seen[0]
    assert isinstance(event, HotkeyPressed)
    assert event.action is HotkeyAction.ACTIVATE

    await service.stop()
    assert backend.active == {}
    assert not backend.started
    await bus.stop()


async def test_settings_change_reregisters_hotkey(tmp_path: Path) -> None:
    backend = FakeBackend()
    service, settings, bus, _ = await make_service(tmp_path, backend)

    await settings.update("activation", activate_hotkey="alt+shift+a")
    await bus.drain(timeout=1)

    assert service.registered[HotkeyAction.ACTIVATE] == "alt+shift+a"
    assert sorted(h for h, _ in backend.active.values()) == [
        "alt+shift+a",
        "ctrl+alt+shift+escape",
    ]
    await service.stop()
    await bus.stop()


async def test_conflict_is_reported_and_previous_hotkey_is_kept(tmp_path: Path) -> None:
    backend = FakeBackend(taken={"ctrl+shift+j"})
    service, settings, bus, seen = await make_service(tmp_path, backend)

    await settings.update("activation", activate_hotkey="ctrl+shift+j")
    await wait_for(lambda: any(isinstance(e, HotkeyRegistrationFailed) for e in seen), bus)

    failure = next(e for e in seen if isinstance(e, HotkeyRegistrationFailed))
    assert failure.hotkey == "ctrl+shift+j"
    assert "em uso" in failure.reason
    assert service.registered[HotkeyAction.ACTIVATE] == "ctrl+alt+space"
    await service.stop()
    await bus.stop()


async def test_startup_conflict_does_not_block_other_hotkeys(tmp_path: Path) -> None:
    backend = FakeBackend(taken={"ctrl+alt+space"})
    service, _, bus, seen = await make_service(tmp_path, backend)
    await bus.drain(timeout=1)

    assert service.registered == {HotkeyAction.EMERGENCY: "ctrl+alt+shift+escape"}
    assert any(isinstance(e, HotkeyRegistrationFailed) for e in seen)
    assert (await service.health()).status == "degraded"
    await service.stop()
    await bus.stop()


def test_failure_message_is_actionable() -> None:
    from alfred.ui.tray import hotkey_failure_message

    message = hotkey_failure_message(
        HotkeyRegistrationFailed(
            action=HotkeyAction.EMERGENCY, hotkey="ctrl+alt+shift+escape", reason="em uso"
        )
    )
    assert "emergência" in message
    assert "settings.json" in message
