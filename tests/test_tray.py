from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from alfred.app import EXIT_OK, bootstrap
from alfred.ui.icon import alfred_icon, render_icon_pixmap
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
from alfred.ui.tray import TrayMenu, TrayModule, TrayUnavailableError


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def test_application_survives_closing_all_windows(qt_app: QApplication) -> None:
    assert qt_app.quitOnLastWindowClosed() is False


def test_icon_renders_all_sizes(qt_app: QApplication) -> None:
    assert render_icon_pixmap(32).width() == 32
    assert len(alfred_icon().availableSizes()) >= 5


def test_menu_exposes_only_real_actions(qt_app: QApplication, tmp_path: Path) -> None:
    quits: list[bool] = []
    toggles: list[bool] = []
    menu = TrayMenu(
        on_quit=lambda: quits.append(True),
        on_toggle_autostart=toggles.append,
        data_dir=tmp_path,
    )

    labels = [a.text() for a in menu.menu.actions() if not a.isSeparator()]
    assert labels[1:] == ["Iniciar com o Windows", "Abrir pasta de dados", "Sair"]

    menu.quit.trigger()
    menu.autostart.trigger()
    assert quits == [True]
    assert toggles == [True]

    menu.set_autostart_checked(False)  # sincronização externa não dispara o callback
    assert toggles == [True]
    assert not menu.autostart.isChecked()


def test_tray_is_optional_when_system_tray_is_unavailable(
    qt_app: QApplication, tmp_path: Path
) -> None:
    app, _ = bootstrap(tmp_path, with_tray=True)

    async def scenario() -> dict[str, str]:
        asyncio.get_running_loop().call_later(0.2, app.request_shutdown)
        code = await app.run(install_signal_handlers=False)
        assert code == EXIT_OK
        return {name: str(state) for name, state in app.manager.states.items()}

    states = run_on_qt(scenario())

    assert TrayModule.optional
    # offscreen não tem bandeja: o módulo falha sozinho e o resto da aplicação roda normalmente
    assert states["tray"] == "failed"
    assert states["database"] == "stopped"


def test_tray_unavailable_error(qt_app: QApplication, tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.settings is not None
    module = TrayModule(
        bus=app.bus, settings=app.settings, data_dir=tmp_path, on_quit=app.request_shutdown
    )

    with pytest.raises(TrayUnavailableError):
        run_on_qt(module.start())
