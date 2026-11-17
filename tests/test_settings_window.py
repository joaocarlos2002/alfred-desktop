from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QSpinBox

from alfred.app import bootstrap
from alfred.config.settings import Settings
from alfred.ui.components import Toggle
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
from alfred.ui.settings_window import SECTIONS, SettingsWindow, SettingsWindowModule


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def window_for(tmp_path: Path) -> tuple[SettingsWindow, object]:
    app, _ = bootstrap(tmp_path)
    assert app.settings is not None
    return SettingsWindow(app.settings), app


def test_every_rf013_section_is_listed(qt_app: QApplication, tmp_path: Path) -> None:
    window, _ = window_for(tmp_path)
    items = [window.sections.item(i) for i in range(window.sections.count())]
    # rótulos de grupo (Essencial, Voz, Inteligência...) não são seções selecionáveis
    titles = [i.text() for i in items if i.data(Qt.ItemDataRole.UserRole) is not None]
    groups = [i.text() for i in items if i.data(Qt.ItemDataRole.UserRole) is None]

    assert len(titles) == len(SECTIONS) == 14
    assert groups == ["ESSENCIAL", "VOZ", "INTELIGÊNCIA", "AUTOMAÇÃO", "PRIVACIDADE E DADOS"]
    for _, title, phase in SECTIONS:
        assert any(t.startswith(title) for t in titles)
        if phase is not None:
            assert f"{title}  ·  em breve" in titles


def test_forms_exist_only_for_real_sections(qt_app: QApplication, tmp_path: Path) -> None:
    window, _ = window_for(tmp_path)
    # Seções com página própria (lista gerenciada, não um formulário de campos).
    assert set(window.forms) == set(Settings.section_names()) - {"skills"}


def test_editors_match_field_types(qt_app: QApplication, tmp_path: Path) -> None:
    window, _ = window_for(tmp_path)

    assert isinstance(window.forms["general"].editor("start_with_windows"), Toggle)
    assert isinstance(window.forms["privacy"].editor("screen_capture"), QComboBox)
    assert isinstance(window.forms["activation"].editor("activate_hotkey"), QLineEdit)
    spin = window.forms["logging"].editor("backup_count")
    assert isinstance(spin, QSpinBox)
    assert (spin.minimum(), spin.maximum()) == (0, 50)
    assert window.forms["appearance"].findChild(QLineEdit, "widget_position") is None
    assert "Atalho global" in window.forms["activation"].editor("activate_hotkey").toolTip()


def test_apply_saves_valid_changes(qt_app: QApplication, tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.settings is not None
    window = SettingsWindow(app.settings)

    async def scenario() -> bool:
        await app.manager.start_all()
        window.forms["appearance"].editor("animations").setChecked(False)  # type: ignore[attr-defined]
        combo = window.forms["privacy"].editor("screen_capture")
        assert isinstance(combo, QComboBox)
        combo.setCurrentIndex(combo.findData("deny"))
        ok = await window.apply()
        await app.manager.stop_all()
        return ok

    assert run_on_qt(scenario())
    assert app.settings.current.appearance.animations is False
    assert app.settings.current.privacy.screen_capture == "deny"
    assert "salvas" in window.feedback.text()


def test_invalid_value_shows_error_and_changes_nothing(
    qt_app: QApplication, tmp_path: Path
) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.settings is not None
    window = SettingsWindow(app.settings)

    async def scenario() -> bool:
        await app.manager.start_all()
        hotkey = window.forms["activation"].editor("activate_hotkey")
        assert isinstance(hotkey, QLineEdit)
        hotkey.setText("space")
        levels = window.forms["logging"].editor("module_levels")
        levels.setPlainText("{ não é json")  # type: ignore[attr-defined]
        ok = await window.apply()
        await app.manager.stop_all()
        return ok

    assert run_on_qt(scenario()) is False
    assert app.settings.current.activation.activate_hotkey == "ctrl+alt+space"
    text = window.feedback.text()
    assert "Não salvo" in text
    assert "Atalho de ativação" in text
    assert "JSON inválido" in text


def test_revert_restores_saved_values(qt_app: QApplication, tmp_path: Path) -> None:
    window, _ = window_for(tmp_path)
    checkbox = window.forms["general"].editor("start_with_windows")
    assert isinstance(checkbox, Toggle)
    checkbox.setChecked(True)

    window.reload()

    assert not window.forms["general"].editor("start_with_windows").isChecked()  # type: ignore[attr-defined]


def test_module_refreshes_form_on_external_change(qt_app: QApplication, tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.settings is not None
    module = SettingsWindowModule(bus=app.bus, settings=app.settings)

    async def scenario() -> bool:
        assert app.settings is not None
        await app.manager.start_all()
        await module.start()
        module.show()
        await app.settings.update("appearance", widget_visible=False)  # ex.: pela bandeja
        await app.bus.drain(timeout=1)
        assert module.window is not None
        visible = module.window.forms["appearance"].editor("widget_visible").isChecked()  # type: ignore[attr-defined]
        await module.stop()
        await app.manager.stop_all()
        return bool(visible)

    assert run_on_qt(scenario()) is False


def test_memory_and_triggers_sections_are_wired_in_the_real_app(
    qt_app: QApplication, tmp_path: Path
) -> None:
    from alfred.ui.settings_window import MemorySection, TriggersSection

    app, _ = bootstrap(tmp_path, with_ui=True)
    module = app.manager._modules["settings_window"]
    assert isinstance(module, SettingsWindowModule)

    async def scenario() -> tuple[list[str], list[str]]:
        await app.manager.start_all()
        module.show()
        window = module.window
        assert window is not None
        memory = window.findChild(MemorySection)
        triggers = window.findChild(TriggersSection)
        assert memory is not None
        assert triggers is not None
        memory.new_item.setText("Prefiro respostas curtas")
        await memory._add()
        triggers.name.setText("Bom dia")
        triggers.kind.setCurrentIndex(triggers.kind.findData("schedule"))
        triggers.source.setText("0 8 * * 1-5")
        triggers.request.setText("leia minha agenda")
        await triggers._save()
        remembered = [m.content for m in await app.memory.memories()]
        saved = [t.name for t in await app.triggers.triggers()]
        triggers.source.setText("todo dia")
        await triggers._save()
        assert "5 campos" in triggers.feedback.text()
        await app.manager.stop_all()
        return remembered, saved

    remembered, saved = run_on_qt(scenario())
    assert remembered == ["Prefiro respostas curtas"]
    assert saved == ["Bom dia"]


def test_memory_section_starts_a_new_conversation(qt_app: QApplication, tmp_path: Path) -> None:
    from alfred.ui.settings_window import MemorySection

    calls: list[str] = []

    class Memory:
        async def memories(self) -> list[object]:
            return []

        async def new_conversation(self) -> None:
            calls.append("nova")

    section = MemorySection(lambda: Memory())

    async def scenario() -> str:
        await section._new_conversation()
        return section.feedback.text()

    assert "Nova conversa" in run_on_qt(scenario())
    assert calls == ["nova"]


def test_skills_section_toggles_a_real_skill(qt_app: QApplication, tmp_path: Path) -> None:
    from typing import ClassVar

    from alfred.skills.base import Skill
    from alfred.ui.settings_window import SkillsSection

    class Demo(Skill):
        key: ClassVar[str] = "demo"
        name: ClassVar[str] = "Demonstração"
        description: ClassVar[str] = "Skill de teste"

    app, _ = bootstrap(tmp_path, skills=(Demo(),))

    async def scenario() -> tuple[bool, bool]:
        await app.manager.start_all()
        section = SkillsSection(lambda: app.skills)
        item = section.list.item(0)
        assert "desligada" in item.text()
        await section._apply(app.skills, "demo", True)
        active = app.skills.statuses()[0].active
        text_ok = "ativa" in section.list.item(0).text()
        await app.manager.stop_all()
        return active, text_ok

    assert run_on_qt(scenario()) == (True, True)
    assert app.settings is not None
    assert "demo" in app.settings.current.skills.enabled


def test_permissions_page_saves_real_policies(qt_app: QApplication, tmp_path: Path) -> None:
    from alfred.core.permissions import Policy
    from alfred.ui.settings_window import PermissionsSection

    app, _ = bootstrap(tmp_path)

    async def scenario() -> tuple[list[str], Policy | None, bool]:
        import asyncio

        await app.manager.start_all()
        section = PermissionsSection(lambda: app.permissions, lambda: app.tools)
        files = section.combos["files"]
        options = [files.itemData(i) for i in range(files.count())]
        section._changed("messages", "deny")
        await asyncio.sleep(0.1)
        assert app.permissions is not None
        saved = app.permissions.category_policy("messages")
        section.reload()
        reloaded = section.combos["messages"].currentData() == "deny"
        await app.manager.stop_all()
        return options, saved, reloaded

    options, saved, reloaded = run_on_qt(scenario())
    assert "allow" not in options  # categoria sensível nunca pode rodar sem confirmação
    assert saved is Policy.DENY
    assert reloaded


def test_memory_empty_state_and_action_bar(qt_app: QApplication, tmp_path: Path) -> None:
    from alfred.ui.settings_window import MemorySection

    app, _ = bootstrap(tmp_path)

    async def scenario() -> tuple[bool, bool, bool, bool]:
        await app.manager.start_all()
        window = SettingsWindow(app.settings, memory=lambda: app.memory)  # type: ignore[arg-type]
        window.show()
        window.select("memory")
        section = window.findChild(MemorySection)
        assert section is not None
        await section.reload()
        empty_shown = section.empty.isVisibleTo(window)
        bar_on_memory = window.actionbar.isVisibleTo(window)
        await app.memory.remember("Prefiro respostas curtas")
        await section.reload()
        list_shown = section.list.isVisibleTo(window) and not section.empty.isVisibleTo(window)
        window.select("general")
        bar_on_form = window.actionbar.isVisibleTo(window)
        await app.manager.stop_all()
        return empty_shown, bar_on_memory, list_shown, bar_on_form

    assert run_on_qt(scenario()) == (True, False, True, True)
