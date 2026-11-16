from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from alfred.automation.window_control import Rect
from alfred.automation.window_service import (
    WindowNotFoundError,
    WindowOps,
    WindowService,
    find_window,
)
from alfred.automation.windows import WindowInfo
from alfred.core.action_log import ActionLog
from alfred.core.commands import CommandRegistry
from alfred.core.event_bus import EventBus
from alfred.core.voice_commands import VoiceCommandHandler
from alfred.domain.activity import ActivityEntry

WINDOWS = [
    WindowInfo(1, "Sem título - Bloco de Notas", 10, "Notepad.exe"),
    WindowInfo(2, "Calculadora", 20, "CalculatorApp.exe"),
    WindowInfo(3, "Relatório.docx - Word", 30, "WINWORD.EXE"),
]


class MemoryActivity:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        self.entries.append(entry)
        return entry


class FakeOps:
    def __init__(self, focus_ok: bool = True) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.rects: dict[int, Rect] = {}
        self.focus_ok = focus_ok

    def ops(self) -> WindowOps:
        return WindowOps(
            list=lambda: list(WINDOWS),
            focus=lambda h: self._record("focus", h) or self.focus_ok,
            minimize=lambda h: self._record("minimize", h),
            maximize=lambda h: self._record("maximize", h),
            restore=lambda h: self._record("restore", h),
            move_resize=lambda h, r: self.rects.__setitem__(h, r),
            get_rect=lambda h: self.rects[h],
        )

    def _record(self, name: str, hwnd: int) -> None:
        self.calls.append((name, hwnd))


def service(fake: FakeOps) -> tuple[WindowService, MemoryActivity]:
    activity = MemoryActivity()
    return WindowService(ActionLog(activity), fake.ops()), activity  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("query", "hwnd"),
    [("calculadora", 2), ("bloco de notas", 1), ("winword", 3), ("relatorio", 3)],
)
def test_find_window(query: str, hwnd: int) -> None:
    assert find_window(WINDOWS, query).hwnd == hwnd


def test_find_window_not_found() -> None:
    with pytest.raises(WindowNotFoundError):
        find_window(WINDOWS, "photoshop")


async def test_state_changes_move_and_logging() -> None:
    fake = FakeOps()
    svc, activity = service(fake)

    await svc.set_state("calculadora", "maximizar")
    await svc.set_state("word", "minimizar")
    moved = await svc.move_window("calculadora", Rect(1920, 100, 400, 600))

    assert fake.calls == [("maximize", 2), ("minimize", 3)]
    assert moved["retangulo"] == {"x": 1920, "y": 100, "width": 400, "height": 600}
    assert [e.action for e in activity.entries] == [
        "maximizar_janela",
        "minimizar_janela",
        "mover_janela",
    ]


async def test_focus_refused_by_windows_is_a_failure_not_a_fake_success() -> None:
    svc, activity = service(FakeOps(focus_ok=False))
    with pytest.raises(Exception, match="não permitiu"):
        await svc.focus_window("calculadora")
    assert activity.entries[-1].status == "failed"


async def test_voice_commands() -> None:
    fake = FakeOps()
    svc, _ = service(fake)
    registry = CommandRegistry()
    svc.register_commands(registry)
    handler = VoiceCommandHandler(EventBus(), emergency=_noop, registry=registry)

    assert (await handler.interpret("Alfred, maximize a calculadora")).reply == (
        "Calculadora maximizada."
    )
    assert (await handler.interpret("mostre o word")).reply == "Pronto, Relatório.docx - Word."
    missing = await handler.interpret("minimize o photoshop")
    assert not missing.understood


async def _noop() -> None:
    return None


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
async def test_real_window_control_on_calculator() -> None:
    from alfred.automation import window_control as wc
    from alfred.automation.apps import AppCatalog, powershell_start_apps
    from alfred.automation.service import AppsService

    activity = MemoryActivity()
    apps = AppsService(AppCatalog(powershell_start_apps), ActionLog(activity), None)  # type: ignore[arg-type]
    svc = WindowService(ActionLog(activity))  # type: ignore[arg-type]
    await apps.open_app("calculadora")
    try:
        hwnd = find_window(svc.ops.list(), "calculadora").hwnd
        await svc.set_state("calculadora", "maximizar")
        await asyncio.sleep(0.4)
        assert wc.is_maximized(hwnd)

        await svc.move_window("calculadora", Rect(120, 140, 420, 640))
        await asyncio.sleep(0.4)
        rect = wc.get_rect(hwnd)
        assert (rect.x, rect.y) == (120, 140)
        assert abs(rect.width - 420) <= 2

        await svc.set_state("calculadora", "minimizar")
        await asyncio.sleep(0.4)
        assert wc.is_minimized(hwnd)

        # Sem input recente, o Windows recusa a troca de foco (anti-roubo de foco): o serviço
        # deve reportar falha — nunca fingir sucesso.
        try:
            await svc.focus_window("calculadora")
            focused_without_input = True
        except wc.WindowControlError:
            focused_without_input = False

        # Caminho real: o ALFRED é acionado por um atalho global; o processo que recebe o
        # WM_HOTKEY recebeu o último input e o Windows permite que ele troque o foco.
        import ctypes
        import threading

        from alfred.infrastructure.hotkeys import Win32HotkeyListener

        listener = Win32HotkeyListener()
        listener.start()
        pressed = threading.Event()
        focused: list[bool] = []

        def on_hotkey() -> None:
            focused.append(wc.focus(hwnd))
            pressed.set()

        listener.register("ctrl+alt+shift+f23", on_hotkey)
        user32 = ctypes.windll.user32
        keys = [0x11, 0x12, 0x10, 0x86]  # ctrl, alt, shift, F23
        for vk in keys:
            user32.keybd_event(vk, 0, 0, 0)
        for vk in reversed(keys):
            user32.keybd_event(vk, 0, 2, 0)
        assert pressed.wait(timeout=3)
        listener.stop()
        assert focused == [True] or focused_without_input
        assert wc.foreground() == hwnd
        assert not wc.is_minimized(hwnd)
    finally:
        await apps.close_app("calculadora")
