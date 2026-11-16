"""Ícone e menu do ALFRED na bandeja do sistema (RF-001).

O menu expõe apenas ações que já existem. Painel e configurações entram com a Fase 2.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from alfred import __version__
from alfred.config.events import SettingsChanged
from alfred.config.settings import Settings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.hotkeys import HotkeyAction, HotkeyRegistrationFailed
from alfred.domain.module import Module
from alfred.ui.icon import alfred_icon

logger = logging.getLogger(__name__)


class TrayUnavailableError(RuntimeError):
    pass


class TrayMenu:
    """Menu da bandeja, independente do ícone para poder ser testado sem system tray."""

    def __init__(
        self,
        *,
        on_quit: Callable[[], None],
        on_toggle_autostart: Callable[[bool], None],
        data_dir: Path,
    ) -> None:
        self.menu = QMenu()
        header = self.menu.addAction(f"ALFRED {__version__}")
        header.setEnabled(False)
        self.menu.addSeparator()

        self.autostart = QAction("Iniciar com o Windows", self.menu)
        self.autostart.setCheckable(True)
        self.autostart.toggled.connect(on_toggle_autostart)
        self.menu.addAction(self.autostart)

        self.open_data = self.menu.addAction("Abrir pasta de dados")
        self.open_data.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir)))
        )
        self.menu.addSeparator()

        self.quit = self.menu.addAction("Sair")
        self.quit.triggered.connect(on_quit)

    def set_autostart_checked(self, checked: bool) -> None:
        blocked = self.autostart.blockSignals(True)
        self.autostart.setChecked(checked)
        self.autostart.blockSignals(blocked)


class TrayModule(Module):
    name = "tray"
    depends_on = ("event_bus", "settings")
    optional = True

    def __init__(
        self,
        *,
        bus: EventBus,
        settings: SettingsService,
        data_dir: Path,
        on_quit: Callable[[], None],
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._data_dir = data_dir
        self._on_quit = on_quit
        self._icon: QSystemTrayIcon | None = None
        self._menu: TrayMenu | None = None
        self._pending: set[asyncio.Task[Settings]] = set()

    async def start(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise TrayUnavailableError("Bandeja do sistema indisponível nesta sessão")
        self._menu = TrayMenu(
            on_quit=self._on_quit,
            on_toggle_autostart=self._toggle_autostart,
            data_dir=self._data_dir,
        )
        self._menu.set_autostart_checked(self._settings.current.general.start_with_windows)
        self._icon = QSystemTrayIcon(alfred_icon())
        self._icon.setToolTip("ALFRED — Always at your service.")
        self._icon.setContextMenu(self._menu.menu)
        self._icon.show()
        self._subscriptions: list[Subscription[Any]] = [
            self._bus.subscribe(SettingsChanged, self._on_settings_changed, name="tray"),
            self._bus.subscribe(
                HotkeyRegistrationFailed, self._on_hotkey_failed, name="tray-hotkeys"
            ),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        if self._icon is not None:
            self._icon.hide()
            self._icon.deleteLater()
        self._icon = None
        self._menu = None

    def _toggle_autostart(self, checked: bool) -> None:
        # Slot do Qt executado no loop asyncio (qasync): agenda a atualização da configuração.
        task = asyncio.ensure_future(self._settings.update("general", start_with_windows=checked))
        self._pending.add(task)
        task.add_done_callback(self._on_toggle_done)

    def _on_toggle_done(self, task: asyncio.Task[Settings]) -> None:
        self._pending.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Falha ao alterar a inicialização automática", exc_info=task.exception())

    async def _on_settings_changed(self, event: SettingsChanged) -> None:
        if self._menu is not None and event.section == "general":
            self._menu.set_autostart_checked(event.settings.general.start_with_windows)

    async def _on_hotkey_failed(self, event: HotkeyRegistrationFailed) -> None:
        if self._icon is not None:
            self._icon.showMessage(
                "Atalho indisponível",
                hotkey_failure_message(event),
                QSystemTrayIcon.MessageIcon.Warning,
            )


def hotkey_failure_message(event: HotkeyRegistrationFailed) -> str:
    label = "de emergência" if event.action is HotkeyAction.EMERGENCY else "de ativação"
    return (
        f"O atalho {label} '{event.hotkey}' não pôde ser registrado: {event.reason}. "
        "Escolha outra combinação em settings.json (seção activation)."
    )
