"""Garante uma única instância do ALFRED por sessão de usuário."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import TracebackType

DEFAULT_INSTANCE_NAME = "Local\\ALFRED-Desktop-SingleInstance"


def instance_name(data_root: Path | None = None) -> str:
    """Uma instância por diretório de dados.

    O diretório padrão usa o nome histórico; um diretório alternativo (``ALFRED_DATA_DIR``,
    ex.: benchmarks) ganha um lock próprio e pode rodar junto com o ALFRED do usuário sem
    disputar banco nem configurações."""
    if data_root is None:
        return DEFAULT_INSTANCE_NAME
    key = str(data_root.resolve()).casefold().encode("utf-8")
    return f"{DEFAULT_INSTANCE_NAME}-{hashlib.sha1(key).hexdigest()[:12]}"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _ERROR_ALREADY_EXISTS = 183
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    class _NamedLock:
        """Mutex nomeado do Windows: existe enquanto houver um handle aberto para ele."""

        def __init__(self, name: str) -> None:
            self._name = name
            self._handle: int | None = None

        def acquire(self) -> bool:
            handle = _kernel32.CreateMutexW(None, False, self._name)
            error = ctypes.get_last_error()
            if not handle:
                raise ctypes.WinError(error)
            if error == _ERROR_ALREADY_EXISTS:
                _kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True

        def release(self) -> None:
            if self._handle is not None:
                _kernel32.CloseHandle(self._handle)
                self._handle = None

else:  # pragma: no cover - ALFRED é exclusivo do Windows; fallback para desenvolvimento
    import fcntl
    import os
    import tempfile
    from pathlib import Path

    class _NamedLock:
        def __init__(self, name: str) -> None:
            safe = "".join(c if c.isalnum() else "_" for c in name)
            self._path = Path(tempfile.gettempdir()) / f"{safe}.lock"
            self._fd: int | None = None

        def acquire(self) -> bool:
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return False
            self._fd = fd
            return True

        def release(self) -> None:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None


class SingleInstance:
    """Lock de instância única. Use como context manager e verifique ``acquired``."""

    def __init__(self, name: str = DEFAULT_INSTANCE_NAME) -> None:
        self._lock = _NamedLock(name)
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def acquire(self) -> bool:
        if not self._acquired:
            self._acquired = self._lock.acquire()
        return self._acquired

    def release(self) -> None:
        if self._acquired:
            self._lock.release()
            self._acquired = False

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
