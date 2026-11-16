"""Enumeração e fechamento gracioso de janelas de nível superior (base para #26)."""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str


def matches_app(window: WindowInfo, app_name: str, normalize: Callable[[str], str]) -> bool:
    wanted = normalize(app_name)
    return bool(wanted) and (
        wanted in normalize(window.title)
        or wanted == normalize(window.process.removesuffix(".exe"))
    )


SHARED_HOSTS = frozenset({"applicationframehost.exe", "explorer.exe", "dwm.exe"})
"""Processos que hospedam janelas de vários apps: nunca são encerrados à força."""


if sys.platform == "win32":
    import psutil
    import win32con
    import win32gui
    import win32process

    def _real_app_pid(hwnd: int, host_pid: int) -> int:
        """Janelas UWP pertencem ao ApplicationFrameHost; o app real é a janela-filha."""
        found: list[int] = []

        def visit(child: int, _: object) -> bool:
            _, pid = win32process.GetWindowThreadProcessId(child)
            if pid != host_pid:
                found.append(int(pid))
                return False
            return True

        with contextlib.suppress(Exception):  # EnumChildWindows sinaliza "parar" como erro
            win32gui.EnumChildWindows(hwnd, visit, None)
        return found[0] if found else host_pid

    def list_windows() -> list[WindowInfo]:
        """Janelas visíveis, com título, de nível superior."""
        found: list[WindowInfo] = []

        def collect(hwnd: int, _: object) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title.strip():
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                process = psutil.Process(pid).name()
                if process.lower() == "applicationframehost.exe":
                    pid = _real_app_pid(hwnd, pid)
                    process = psutil.Process(pid).name()
            except (psutil.Error, OSError):
                process = ""
            found.append(WindowInfo(int(hwnd), title, int(pid), process))
            return True

        win32gui.EnumWindows(collect, None)
        return found

    def request_close(hwnd: int) -> None:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    def is_window(hwnd: int) -> bool:
        return bool(win32gui.IsWindow(hwnd))

    def terminate(pids: Iterable[int]) -> None:
        for pid in set(pids):
            try:
                process = psutil.Process(pid)
                if process.name().lower() in SHARED_HOSTS:
                    continue  # derrubaria outros apps junto
                process.terminate()
            except psutil.Error:
                continue

else:  # pragma: no cover

    def list_windows() -> list[WindowInfo]:
        return []

    def request_close(hwnd: int) -> None:
        return None

    def is_window(hwnd: int) -> bool:
        return False

    def terminate(pids: Iterable[int]) -> None:
        return None


def wait_until_closed(
    hwnds: Iterable[int],
    timeout: float,
    *,
    alive: Callable[[int], bool] = is_window,
    sleep: Callable[[float], None] = time.sleep,
) -> list[int]:
    """Espera as janelas fecharem; devolve as que continuam abertas após ``timeout``."""
    pending = list(hwnds)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        pending = [h for h in pending if alive(h)]
        if pending:
            sleep(0.1)
    return [h for h in pending if alive(h)]
