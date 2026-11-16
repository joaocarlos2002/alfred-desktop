"""Raiz de composição: instancia os módulos e conduz o ciclo de vida da aplicação."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from alfred.config.events import SettingsChanged
from alfred.config.settings import LoggingSettings
from alfred.config.store import SETTINGS_FILE, LoadResult, SettingsStore
from alfred.core.assistant_state import AssistantStateService
from alfred.core.autostart_service import AutostartBackend, AutostartService
from alfred.core.confirmations import ConfirmationService
from alfred.core.emergency import EmergencyStop
from alfred.core.event_bus import EventBus
from alfred.core.hotkey_service import HotkeyService
from alfred.core.lifecycle import ModuleManager, StartupError
from alfred.core.settings_service import SettingsService
from alfred.core.tasks import TaskRegistry
from alfred.domain.emergency import InputReleaser
from alfred.domain.events import Event
from alfred.domain.hotkeys import HotkeyBackend
from alfred.domain.module import Module
from alfred.infrastructure.db.activity_repository import SqlActivityLogRepository
from alfred.infrastructure.db.database import DB_FILE, Database
from alfred.infrastructure.logging_setup import LoggingOptions, apply_levels

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1
EXIT_ALREADY_RUNNING = 2

_SHUTDOWN_SIGNALS = tuple(
    getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, name)
)


class Application:
    """Inicia os módulos, aguarda um pedido de encerramento e encerra tudo de forma graciosa."""

    def __init__(self, manager: ModuleManager, bus: EventBus) -> None:
        self.manager = manager
        self.bus = bus
        self.settings: SettingsService | None = None
        self.database: Database | None = None
        self.tasks: TaskRegistry | None = None
        self.emergency: EmergencyStop | None = None
        self.confirmations: ConfirmationService | None = None
        self._shutdown = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def request_shutdown(self) -> None:
        """Pede o encerramento. Seguro para chamar de qualquer thread."""
        if self._loop is None:
            self._shutdown.set()
        else:
            self._loop.call_soon_threadsafe(self._shutdown.set)

    async def run(self, *, install_signal_handlers: bool = True) -> int:
        self._loop = asyncio.get_running_loop()
        with _signal_handlers(self.request_shutdown, enabled=install_signal_handlers):
            try:
                await self.manager.start_all()
            except StartupError:
                logger.exception("ALFRED não pôde ser iniciado")
                return EXIT_STARTUP_FAILED
            logger.info("ALFRED em execução")
            try:
                await self._shutdown.wait()
            finally:
                logger.info("Encerrando ALFRED")
                await self.manager.stop_all()
        return EXIT_OK


def create_application(
    modules: Iterable[Module] = (), *, bus: EventBus | None = None
) -> Application:
    """Monta a aplicação. O event bus é sempre o primeiro módulo registrado."""
    bus = bus or EventBus()
    bus.add_tap(_audit_event)
    manager = ModuleManager()
    manager.register(bus)
    for module in modules:
        manager.register(module)
    return Application(manager, bus)


def bootstrap(
    data_root: Path,
    *,
    autostart: AutostartBackend | None = None,
    hotkeys: HotkeyBackend | None = None,
    input_releaser: InputReleaser | None = None,
    with_ui: bool = False,
) -> tuple[Application, LoadResult]:
    """Monta a aplicação padrão a partir do diretório de dados.

    As configurações são carregadas de forma síncrona, antes do logging, para que o logging já
    nasça configurado; os avisos do carregamento vêm em ``LoadResult.warnings``.

    ``autostart``, ``hotkeys``, ``input_releaser`` e ``with_ui`` só são ligados pelo executável
    real, para que testes nunca toquem no registro do Windows, nos atalhos globais, no teclado e
    mouse nem na bandeja do sistema.
    """
    store = SettingsStore(data_root / SETTINGS_FILE)
    loaded = store.load()
    bus = EventBus()
    settings = SettingsService(store, bus, initial=loaded.settings)

    async def on_settings_changed(event: SettingsChanged) -> None:
        if event.section == "logging":
            current = event.settings.logging
            apply_levels(current.level, current.module_levels)

    bus.subscribe(SettingsChanged, on_settings_changed, name="logging-levels")
    database = Database(data_root / DB_FILE)
    tasks = TaskRegistry()
    emergency = EmergencyStop(
        tasks,
        bus,
        input_releaser=input_releaser,
        activity=SqlActivityLogRepository(database),
    )
    activity = SqlActivityLogRepository(database)
    confirmations = ConfirmationService(bus, activity=activity)
    modules: list[Module] = [
        settings,
        database,
        emergency,
        confirmations,
        AssistantStateService(bus),
    ]
    if autostart is not None:
        modules.append(AutostartService(autostart, settings, bus))
    if hotkeys is not None:
        modules.append(
            HotkeyService(hotkeys, settings, bus, on_emergency=emergency.trigger_threadsafe)
        )
    app = create_application(modules, bus=bus)
    app.settings = settings
    app.database = database
    app.tasks = tasks
    app.emergency = emergency
    app.confirmations = confirmations
    if with_ui:
        from alfred.ui.confirmation_panel import ConfirmationUiModule
        from alfred.ui.tray import TrayModule
        from alfred.ui.widget import WidgetModule

        app.manager.register(WidgetModule(bus=bus, settings=settings))
        app.manager.register(ConfirmationUiModule(bus=bus, service=confirmations))
        app.manager.register(
            TrayModule(bus=bus, settings=settings, data_dir=data_root, on_quit=app.request_shutdown)
        )
    return app, loaded


def logging_options(
    settings: LoggingSettings, log_dir: Path | None, *, level_override: str | None = None
) -> LoggingOptions:
    return LoggingOptions(
        level=level_override or settings.level,
        module_levels=dict(settings.module_levels),
        log_dir=log_dir,
        max_bytes=settings.max_bytes,
        backup_count=settings.backup_count,
    )


_event_logger = logging.getLogger("alfred.events")


def _audit_event(event: Event) -> None:
    """Registra todo evento publicado (nível DEBUG) para auditoria."""
    if _event_logger.isEnabledFor(logging.DEBUG):
        _event_logger.debug(
            "%s",
            type(event).__name__,
            extra={
                "event_id": str(event.id),
                "event_correlation_id": str(event.correlation_id),
                "event_source": event.source,
            },
        )


@contextmanager
def _signal_handlers(callback: Callable[[], None], *, enabled: bool) -> Iterator[None]:
    if not enabled:
        yield
        return

    def handler(signum: int, frame: FrameType | None) -> None:
        logger.info("Sinal %s recebido", signal.Signals(signum).name)
        callback()

    previous = {sig: signal.signal(sig, handler) for sig in _SHUTDOWN_SIGNALS}
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
