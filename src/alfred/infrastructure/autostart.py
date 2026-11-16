"""Inicialização automática com o Windows (chave ``Run`` do usuário atual)."""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ALFRED"


def default_command() -> str:
    """Linha de comando que inicia o ALFRED sem janela de console."""
    if getattr(sys, "frozen", False):  # executável empacotado (PyInstaller/Nuitka)
        return f'"{sys.executable}"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else python
    return f'"{interpreter}" -m alfred'


if sys.platform == "win32":
    import winreg

    class Autostart:
        """Lê e grava o valor ``ALFRED`` em ``HKCU\\...\\Run``."""

        def __init__(self, key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> None:
            self._key_path = key_path
            self._value_name = value_name

        def command(self) -> str | None:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key_path) as key:
                    value, kind = winreg.QueryValueEx(key, self._value_name)
            except FileNotFoundError:
                return None
            return str(value) if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else None

        def is_enabled(self) -> bool:
            return self.command() is not None

        def enable(self, command: str | None = None) -> None:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, self._key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(
                    key, self._value_name, 0, winreg.REG_SZ, command or default_command()
                )

        def disable(self) -> None:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, self._key_path, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.DeleteValue(key, self._value_name)
            except FileNotFoundError:
                pass

else:  # pragma: no cover - ALFRED é exclusivo do Windows

    class Autostart:
        def __init__(self, key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> None:
            pass

        def command(self) -> str | None:
            return None

        def is_enabled(self) -> bool:
            return False

        def enable(self, command: str | None = None) -> None:
            raise OSError("Inicialização automática disponível apenas no Windows")

        def disable(self) -> None:
            pass
