"""Solta teclas modificadoras e botões do mouse pressionados (usado pela emergência)."""

from __future__ import annotations

import sys

from alfred.domain.emergency import InputReleaser

KEYEVENTF_KEYUP = 0x0002

MODIFIER_KEYS = {
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "lwin": 0x5B,
    "rwin": 0x5C,
}
MOUSE_BUTTONS = {
    # nome: (virtual-key, flag de "botão solto" para mouse_event)
    "mouse_left": (0x01, 0x0004),
    "mouse_right": (0x02, 0x0010),
    "mouse_middle": (0x04, 0x0040),
}

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    _user32.keybd_event.restype = None
    _user32.mouse_event.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    _user32.mouse_event.restype = None

    def _is_down(vk: int) -> bool:
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

    class Win32InputReleaser(InputReleaser):
        def release_all(self) -> list[str]:
            released: list[str] = []
            for name, vk in MODIFIER_KEYS.items():
                if _is_down(vk):
                    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                    released.append(name)
            for name, (vk, up_flag) in MOUSE_BUTTONS.items():
                if _is_down(vk):
                    _user32.mouse_event(up_flag, 0, 0, 0, 0)
                    released.append(name)
            return released

        @staticmethod
        def is_down(name: str) -> bool:
            vk = MODIFIER_KEYS.get(name) or MOUSE_BUTTONS[name][0]
            return _is_down(vk)
