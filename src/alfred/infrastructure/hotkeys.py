"""Atalhos globais via ``RegisterHotKey`` do Win32.

``RegisterHotKey`` associa o atalho à fila de mensagens da thread que o registrou, então registro,
remoção e recebimento de ``WM_HOTKEY`` acontecem todos numa thread dedicada com message loop.
Pedidos de outras threads chegam por ``PostThreadMessageW``.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from alfred.domain.hotkeys import HotkeyBackend, HotkeyConflictError

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIER_FLAGS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "pause": 0x13,
    **{f"f{n}": 0x70 + n - 1 for n in range(1, 25)},
}


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    """``"ctrl+alt+space"`` → (modificadores, virtual-key code)."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"atalho '{hotkey}' precisa de modificador e tecla")
    *modifiers, key = parts
    flags = 0
    for modifier in modifiers:
        if modifier not in _MODIFIER_FLAGS:
            raise ValueError(f"modificador desconhecido em '{hotkey}': {modifier}")
        flags |= _MODIFIER_FLAGS[modifier]
    if key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    else:
        raise ValueError(f"tecla desconhecida em '{hotkey}': {key}")
    return flags, vk


@dataclass
class _Request:
    kind: str  # "register" | "unregister"
    args: tuple[Any, ...]
    result: Future[Any] = field(default_factory=Future)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.PeekMessageW.restype = wintypes.BOOL
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WM_APP_REQUEST = 0x8000 + 1
    PM_NOREMOVE = 0x0000
    ERROR_HOTKEY_ALREADY_REGISTERED = 1409

    class Win32HotkeyListener(HotkeyBackend):
        def __init__(self) -> None:
            self._thread: threading.Thread | None = None
            self._thread_id: int | None = None
            self._ready = threading.Event()
            self._requests: queue.SimpleQueue[_Request] = queue.SimpleQueue()
            self._callbacks: dict[int, Callable[[], None]] = {}
            self._next_id = 1

        # -------------------------------------------------------------- API (qualquer thread)
        def start(self) -> None:
            if self._thread is not None:
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="alfred-hotkeys", daemon=True)
            self._thread.start()
            if not self._ready.wait(timeout=5):
                raise RuntimeError("Thread de atalhos não iniciou")

        def stop(self) -> None:
            if self._thread is None or self._thread_id is None:
                return
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=5)
            self._thread = None
            self._thread_id = None

        def register(self, hotkey: str, callback: Callable[[], None]) -> int:
            modifiers, vk = parse_hotkey(hotkey)
            result: int = self._submit("register", hotkey, modifiers, vk, callback)
            return result

        def unregister(self, hotkey_id: int) -> None:
            self._submit("unregister", hotkey_id)

        def _submit(self, kind: str, *args: Any) -> Any:
            if self._thread_id is None:
                raise RuntimeError("Listener de atalhos não iniciado")
            request = _Request(kind, args)
            self._requests.put(request)
            _user32.PostThreadMessageW(self._thread_id, WM_APP_REQUEST, 0, 0)
            return request.result.result(timeout=5)

        # -------------------------------------------------------------- thread de mensagens
        def _run(self) -> None:
            msg = wintypes.MSG()
            # Força a criação da fila de mensagens antes de sinalizar que está pronta.
            _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
            self._thread_id = int(_kernel32.GetCurrentThreadId())
            self._ready.set()
            try:
                while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == WM_HOTKEY:
                        self._dispatch(int(msg.wParam))
                    elif msg.message == WM_APP_REQUEST:
                        self._process_requests()
            finally:
                for hotkey_id in list(self._callbacks):
                    _user32.UnregisterHotKey(None, hotkey_id)
                self._callbacks.clear()

        def _dispatch(self, hotkey_id: int) -> None:
            callback = self._callbacks.get(hotkey_id)
            if callback is None:
                return
            try:
                callback()
            except Exception:
                logger.exception("Callback de atalho falhou")

        def _process_requests(self) -> None:
            while True:
                try:
                    request = self._requests.get_nowait()
                except queue.Empty:
                    return
                try:
                    if request.kind == "register":
                        request.result.set_result(self._do_register(*request.args))
                    else:
                        self._do_unregister(*request.args)
                        request.result.set_result(None)
                except Exception as exc:
                    request.result.set_exception(exc)

        def _do_register(
            self, hotkey: str, modifiers: int, vk: int, callback: Callable[[], None]
        ) -> int:
            hotkey_id = self._next_id
            if not _user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
                error = ctypes.get_last_error()
                if error == ERROR_HOTKEY_ALREADY_REGISTERED:
                    raise HotkeyConflictError(f"'{hotkey}' já está em uso por outro programa")
                raise ctypes.WinError(error)
            self._next_id += 1
            self._callbacks[hotkey_id] = callback
            return hotkey_id

        def _do_unregister(self, hotkey_id: int) -> None:
            if self._callbacks.pop(hotkey_id, None) is not None:
                _user32.UnregisterHotKey(None, hotkey_id)
