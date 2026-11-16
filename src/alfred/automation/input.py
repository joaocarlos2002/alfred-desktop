"""Teclado e mouse via ``SendInput`` (usar só quando não houver API/UIA — Diretriz 4)."""

from __future__ import annotations

import sys
import time

from alfred.infrastructure.hotkeys import parse_hotkey

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_FOR_MODIFIER = {0x0002: 0x11, 0x0001: 0x12, 0x0004: 0x10, 0x0008: 0x5B}  # ctrl alt shift win
MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32")
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT

    def _send(inputs: list[INPUT]) -> None:
        array = (INPUT * len(inputs))(*inputs)
        sent = _user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise ctypes.WinError(ctypes.get_last_error())

    def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
        item = INPUT(type=1)
        item.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        return item

    def type_text(text: str) -> None:
        """Digita texto Unicode (acentos e emojis) na janela em foco."""
        events: list[INPUT] = []
        for unit in text.encode("utf-16-le").hex(" ", 2).split():
            code = int.from_bytes(bytes.fromhex(unit), "little")
            events.append(_key(scan=code, flags=KEYEVENTF_UNICODE))
            events.append(_key(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        if events:
            _send(events)

    def press_hotkey(hotkey: str) -> None:
        """Pressiona e solta uma combinação (ex.: ``ctrl+s``)."""
        modifiers, vk = parse_hotkey(hotkey)
        mods = [VK_FOR_MODIFIER[flag] for flag in VK_FOR_MODIFIER if modifiers & flag]
        down = [_key(m) for m in mods] + [_key(vk)]
        up = [_key(vk, flags=KEYEVENTF_KEYUP)] + [
            _key(m, flags=KEYEVENTF_KEYUP) for m in reversed(mods)
        ]
        _send(down + up)

    def key_down(vk: int) -> None:
        _send([_key(vk)])

    def key_up(vk: int) -> None:
        _send([_key(vk, flags=KEYEVENTF_KEYUP)])

    def click(x: int, y: int, button: str = "left") -> None:
        """Move o cursor físico até (x, y) em pixels físicos e clica."""
        down, up = MOUSE_FLAGS[button]
        _user32.SetCursorPos(int(x), int(y))
        events = []
        for flag in (down, up):
            item = INPUT(type=0)
            item.u.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
            events.append(item)
        _send(events)

    def idle_ms() -> int:
        """Milissegundos desde o último input (de qualquer origem) no sistema."""
        info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        _user32.GetLastInputInfo(ctypes.byref(info))
        return int(_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF

else:  # pragma: no cover

    def _unsupported(*_: object) -> None:
        raise OSError("Input simulado disponível apenas no Windows")

    type_text = press_hotkey = key_down = key_up = click = _unsupported  # type: ignore[assignment]

    def idle_ms() -> int:
        return 10**9


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
