from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from alfred.config.store import SettingsStore
from alfred.core.autostart_service import AutostartService
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.infrastructure.autostart import Autostart, default_command

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="registro do Windows")


@pytest.fixture
def autostart() -> Iterator[Autostart]:
    """Usa uma chave de teste isolada — nunca a chave Run real do usuário."""
    import winreg

    key_path = rf"Software\ALFRED-tests\{uuid.uuid4()}"
    yield Autostart(key_path=key_path, value_name="ALFRED")
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)


def test_enable_disable_roundtrip(autostart: Autostart) -> None:
    assert not autostart.is_enabled()

    autostart.enable('"C:\\ALFRED\\alfred.exe"')
    assert autostart.command() == '"C:\\ALFRED\\alfred.exe"'
    assert autostart.is_enabled()

    autostart.disable()
    autostart.disable()  # idempotente
    assert not autostart.is_enabled()


def test_default_command_runs_module_without_console() -> None:
    command = default_command()
    assert command.endswith("-m alfred")
    assert "pythonw.exe" in command.lower() or "python" in command.lower()


class FakeBackend:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.calls: list[str] = []

    def is_enabled(self) -> bool:
        return self.enabled

    def enable(self, command: str | None = None) -> None:
        self.calls.append("enable")
        self.enabled = True

    def disable(self) -> None:
        self.calls.append("disable")
        self.enabled = False


async def test_service_reconciles_on_start_and_follows_settings(tmp_path: Path) -> None:
    bus = EventBus()
    await bus.start()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    backend = FakeBackend(enabled=True)  # registro divergente da configuração (False)
    service = AutostartService(backend, settings, bus)

    await service.start()
    assert backend.calls == ["disable"]

    await settings.update("general", start_with_windows=True)
    await bus.drain(timeout=1)
    assert backend.calls == ["disable", "enable"]

    await settings.update("appearance", animations=False)
    await bus.drain(timeout=1)
    assert backend.calls == ["disable", "enable"]

    await service.stop()
    await bus.stop()
