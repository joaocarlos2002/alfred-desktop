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
from alfred.domain.emergency import EmergencyStopped
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
        on_toggle_widget: Callable[[bool], None] | None = None,
    ) -> None:
        self.menu = QMenu()
        header = self.menu.addAction(f"ALFRED {__version__}")
        header.setEnabled(False)
        self.menu.addSeparator()

        self.widget: QAction | None = None
        if on_toggle_widget is not None:
            self.widget = QAction("Mostrar widget", self.menu)
            self.widget.setCheckable(True)
            self.widget.toggled.connect(on_toggle_widget)
            self.menu.addAction(self.widget)

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
        _set_checked_silently(self.autostart, checked)

    def set_widget_checked(self, checked: bool) -> None:
        if self.widget is not None:
            _set_checked_silently(self.widget, checked)


def _set_checked_silently(action: QAction, checked: bool) -> None:
    """Sincroniza o estado sem disparar o callback (evita laço configuração ↔ menu)."""
    blocked = action.blockSignals(True)
    action.setChecked(checked)
    action.blockSignals(blocked)


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
            on_toggle_widget=self._toggle_widget,
            data_dir=self._data_dir,
        )
        self._menu.set_autostart_checked(self._settings.current.general.start_with_windows)
        self._menu.set_widget_checked(self._settings.current.appearance.widget_visible)
        self._icon = QSystemTrayIcon(alfred_icon())
        self._icon.setToolTip("ALFRED — Always at your service.")
        self._icon.setContextMenu(self._menu.menu)
        self._icon.show()
        self._subscriptions: list[Subscription[Any]] = [
            self._bus.subscribe(SettingsChanged, self._on_settings_changed, name="tray"),
            self._bus.subscribe(
                HotkeyRegistrationFailed, self._on_hotkey_failed, name="tray-hotkeys"
            ),
            self._bus.subscribe(EmergencyStopped, self._on_emergency, name="tray-emergency"),
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
        self._update_setting("general", start_with_windows=checked)

    def _toggle_widget(self, checked: bool) -> None:
        self._update_setting("appearance", widget_visible=checked)

    def _update_setting(self, section: str, **changes: Any) -> None:
        # Slot do Qt executado no loop asyncio (qasync): agenda a atualização da configuração.
        task = asyncio.ensure_future(self._settings.update(section, **changes))
        self._pending.add(task)
        task.add_done_callback(self._on_update_done)

    def _on_update_done(self, task: asyncio.Task[Settings]) -> None:
        self._pending.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Falha ao alterar configuração pela bandeja", exc_info=task.exception())

    async def _on_settings_changed(self, event: SettingsChanged) -> None:
        if self._menu is None:
            return
        if event.section == "general":
            self._menu.set_autostart_checked(event.settings.general.start_with_windows)
        elif event.section == "appearance":
            self._menu.set_widget_checked(event.settings.appearance.widget_visible)

    async def _on_emergency(self, event: EmergencyStopped) -> None:
        if self._icon is not None:
            self._icon.showMessage(
                "Interrupção de emergência",
                emergency_message(event),
                QSystemTrayIcon.MessageIcon.Information,
            )

    async def _on_hotkey_failed(self, event: HotkeyRegistrationFailed) -> None:
        if self._icon is not None:
            self._icon.showMessage(
                "Atalho indisponível",
                hotkey_failure_message(event),
                QSystemTrayIcon.MessageIcon.Warning,
            )


def emergency_message(event: EmergencyStopped) -> str:
    message = f"{event.cancelled_tasks} tarefa(s) cancelada(s) em {event.elapsed_ms:.0f} ms."
    if event.stuck_tasks:
        message += f" {event.stuck_tasks} não respondeu(ram) ao cancelamento."
    if event.released_inputs:
        message += f" Liberado: {', '.join(event.released_inputs)}."
    return message


def hotkey_failure_message(event: HotkeyRegistrationFailed) -> str:
    label = "de emergência" if event.action is HotkeyAction.EMERGENCY else "de ativação"
    return (
        f"O atalho {label} '{event.hotkey}' não pôde ser registrado: {event.reason}. "
        "Escolha outra combinação em settings.json (seção activation)."
    )
