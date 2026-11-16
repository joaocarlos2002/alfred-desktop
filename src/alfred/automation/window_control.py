"""Operações sobre janelas de nível superior (RF-006): foco, estado, posição e tamanho.

Coordenadas em pixels físicos do desktop virtual (multi-monitor).

Foco: o Windows só deixa um processo trazer janelas para frente se ele recebeu o último evento de
input — caso real do ALFRED, acionado por atalho global (verificado em teste). Sem isso, tentamos
``AttachThreadInput``, um toque de ALT e ``SwitchToThisWindow``; se ainda assim o Windows recusar,
a função devolve ``False`` e o serviço reporta a falha (nunca um sucesso fictício).
"""

from __future__ import annotations

import contextlib
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


class WindowControlError(RuntimeError):
    pass


if sys.platform == "win32":
    import ctypes

    import win32api
    import win32con
    import win32gui
    import win32process

    _user32 = ctypes.windll.user32

    def get_rect(hwnd: int) -> Rect:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return Rect(left, top, right - left, bottom - top)

    def is_minimized(hwnd: int) -> bool:
        return bool(win32gui.IsIconic(hwnd))

    def is_maximized(hwnd: int) -> bool:
        return bool(_user32.IsZoomed(hwnd))

    def foreground() -> int:
        return int(win32gui.GetForegroundWindow())

    def minimize(hwnd: int) -> None:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

    def maximize(hwnd: int) -> None:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)

    def restore(hwnd: int) -> None:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    def move_resize(hwnd: int, rect: Rect) -> None:
        if is_minimized(hwnd) or is_maximized(hwnd):
            restore(hwnd)
        flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
        win32gui.SetWindowPos(hwnd, 0, rect.x, rect.y, rect.width, rect.height, flags)

    def focus(hwnd: int) -> bool:
        """Traz a janela para frente; devolve se ela realmente ficou em primeiro plano."""
        if is_minimized(hwnd):
            restore(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            current = foreground()
            current_thread, _ = win32process.GetWindowThreadProcessId(current)
            own_thread = win32api.GetCurrentThreadId()
            attached = bool(_user32.AttachThreadInput(own_thread, current_thread, True))
            try:
                win32gui.BringWindowToTop(hwnd)
                with contextlib.suppress(Exception):
                    win32gui.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    _user32.AttachThreadInput(own_thread, current_thread, False)
        if foreground() != hwnd:
            # Regra anti-roubo de foco: o Windows libera a troca logo após um evento de input.
            # Um toque de ALT (sem efeito visível) é o contorno usado por pywinauto/AutoHotkey.
            _user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
            _user32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            with contextlib.suppress(Exception):
                win32gui.SetForegroundWindow(hwnd)
        if foreground() != hwnd:
            _user32.SwitchToThisWindow(hwnd, True)  # mesma API usada pelo Alt+Tab
            time.sleep(0.15)
        return foreground() == hwnd

else:  # pragma: no cover

    def _unsupported(*_: object) -> None:
        raise WindowControlError("Controle de janelas disponível apenas no Windows")

    get_rect = is_minimized = is_maximized = foreground = _unsupported  # type: ignore[assignment]
    minimize = maximize = restore = move_resize = focus = _unsupported  # type: ignore[assignment]
