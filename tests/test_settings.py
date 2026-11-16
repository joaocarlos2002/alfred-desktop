from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alfred.app import bootstrap
from alfred.config.events import SettingsChanged
from alfred.config.settings import CURRENT_SCHEMA_VERSION, Settings, normalize_hotkey
from alfred.config.store import SETTINGS_FILE, SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService


def write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / SETTINGS_FILE


# ------------------------------------------------------------------ modelos
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ctrl + Alt + Space", "ctrl+alt+space"),
        ("shift+ctrl+K", "ctrl+shift+k"),
        ("win+alt+1", "alt+win+1"),
    ],
)
def test_hotkeys_are_normalized(raw: str, expected: str) -> None:
    assert normalize_hotkey(raw) == expected


@pytest.mark.parametrize("raw", ["space", "ctrl+alt", "ctrl+ctrl+a", "hyper+a", "", "ctrl+@"])
def test_invalid_hotkeys_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match=r"atalho|modificador"):
        normalize_hotkey(raw)


def test_with_section_validates_and_rejects_unknown_fields() -> None:
    settings = Settings()

    updated = settings.with_section("activation", activate_hotkey="Alt+Shift+A")
    assert updated.activation.activate_hotkey == "alt+shift+a"
    assert settings.activation.activate_hotkey == "ctrl+alt+space"

    with pytest.raises(KeyError):
        settings.with_section("activation", nope=1)
    with pytest.raises(KeyError):
        settings.with_section("ghost", x=1)
    with pytest.raises(ValidationError):
        settings.with_section("logging", level="LOUD")


# ------------------------------------------------------------------ store
def test_missing_file_creates_defaults(path: Path) -> None:
    result = SettingsStore(path).load()

    assert result.settings == Settings()
    assert result.warnings == ()
    assert json.loads(path.read_text("utf-8"))["schema_version"] == CURRENT_SCHEMA_VERSION


def test_roundtrip(path: Path) -> None:
    store = SettingsStore(path)
    custom = Settings().with_section("privacy", screen_capture="deny")
    store.save(custom)

    assert store.load().settings == custom


def test_corrupt_file_is_backed_up_and_defaults_restored(path: Path) -> None:
    write(path, "{ isto não é json")

    result = SettingsStore(path).load()

    assert result.settings == Settings()
    assert "ilegíveis" in result.warnings[0]
    backups = list(path.parent.glob("settings.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text("utf-8") == "{ isto não é json"


def test_invalid_field_falls_back_without_losing_siblings(path: Path) -> None:
    write(
        path,
        {
            "schema_version": 1,
            "activation": {"activate_hotkey": "alt+shift+a", "emergency_hotkey": "escape"},
            "logging": {"level": "DEBUG"},
        },
    )

    result = SettingsStore(path).load()

    assert result.settings.activation.activate_hotkey == "alt+shift+a"
    assert result.settings.activation.emergency_hotkey == "ctrl+alt+shift+escape"
    assert result.settings.logging.level == "DEBUG"
    assert any("activation.emergency_hotkey" in w for w in result.warnings)
    saved = json.loads(path.read_text("utf-8"))
    assert saved["activation"]["emergency_hotkey"] == "ctrl+alt+shift+escape"


def test_unknown_sections_and_fields_are_ignored(path: Path) -> None:
    write(path, {"schema_version": 1, "voice": {"x": 1}, "general": {"legacy": True}})

    result = SettingsStore(path).load()

    assert result.settings == Settings()
    assert any("voice" in w for w in result.warnings)


def test_migration_is_applied_and_original_backed_up(path: Path) -> None:
    def v0_to_v1(raw: dict[str, Any]) -> dict[str, Any]:
        raw["activation"] = {"activate_hotkey": raw.pop("hotkey")}
        raw["schema_version"] = 1
        return raw

    write(path, {"schema_version": 0, "hotkey": "ctrl+shift+j"})

    result = SettingsStore(path, migrations={0: v0_to_v1}).load()

    assert result.settings.activation.activate_hotkey == "ctrl+shift+j"
    assert list(path.parent.glob("settings.json.v0-*.bak"))
    assert json.loads(path.read_text("utf-8"))["schema_version"] == 1


def test_newer_schema_is_read_but_not_overwritten(path: Path) -> None:
    original = {"schema_version": 99, "logging": {"level": "ERROR"}, "future": {}}
    write(path, original)

    result = SettingsStore(path).load()

    assert result.settings.logging.level == "ERROR"
    assert any("mais nova" in w for w in result.warnings)
    assert json.loads(path.read_text("utf-8")) == original


# ------------------------------------------------------------------ service
async def test_update_persists_and_publishes(path: Path) -> None:
    bus = EventBus()
    await bus.start()
    store = SettingsStore(path)
    service = SettingsService(store, bus)
    await service.start()
    received: list[SettingsChanged] = []

    async def on_change(event: SettingsChanged) -> None:
        received.append(event)

    bus.subscribe(SettingsChanged, on_change)

    await service.update("appearance", animations=False, theme="dark")
    await service.update("appearance", animations=False)  # sem mudança: sem evento
    await bus.drain(timeout=1)

    assert service.current.appearance.animations is False
    assert store.load().settings.appearance.animations is False
    assert len(received) == 1
    assert received[0].section == "appearance"
    assert received[0].changed == ("animations",)
    await bus.stop()


async def test_invalid_update_changes_nothing(path: Path) -> None:
    bus = EventBus()
    service = SettingsService(SettingsStore(path), bus, initial=Settings())

    with pytest.raises(ValidationError):
        await service.update("logging", backup_count=-1)

    assert service.current == Settings()
    assert not path.exists()  # noqa: ASYNC240 - verificação síncrona trivial em teste


async def test_bootstrap_applies_logging_levels_at_runtime(tmp_path: Path) -> None:
    app, loaded = bootstrap(tmp_path)
    assert loaded.settings == Settings()
    assert app.settings is not None
    await app.bus.start()
    root_level = logging.getLogger().level
    try:
        await app.settings.update("logging", level="ERROR", module_levels={"alfred.voice": "DEBUG"})
        await app.bus.drain(timeout=1)

        assert logging.getLogger().level == logging.ERROR
        assert logging.getLogger("alfred.voice").level == logging.DEBUG

        await app.settings.update("logging", module_levels={})
        await app.bus.drain(timeout=1)
        assert logging.getLogger("alfred.voice").level == logging.NOTSET
    finally:
        logging.getLogger().setLevel(root_level)
        await app.bus.stop()
