"""Raiz de composição: instancia os módulos e conduz o ciclo de vida da aplicação."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

from alfred.ai.providers import AIService, ProviderRegistry
from alfred.config.events import SettingsChanged
from alfred.config.settings import LoggingSettings
from alfred.config.store import SETTINGS_FILE, LoadResult, SettingsStore
from alfred.core.action_log import ActionLog
from alfred.core.assistant_state import AssistantStateService
from alfred.core.autostart_service import AutostartBackend, AutostartService
from alfred.core.commands import CommandRegistry
from alfred.core.confirmations import ConfirmationService
from alfred.core.emergency import EmergencyStop
from alfred.core.event_bus import EventBus
from alfred.core.hotkey_service import HotkeyService
from alfred.core.input_lock import InputLock
from alfred.core.lifecycle import ModuleManager, StartupError
from alfred.core.orchestrator import Orchestrator
from alfred.core.permissions import PermissionService
from alfred.core.settings_service import SettingsService
from alfred.core.task_tracker import TaskTracker
from alfred.core.tasks import TaskRegistry
from alfred.core.tools import ToolRegistry
from alfred.core.voice_commands import VoiceCommandHandler
from alfred.domain.emergency import InputReleaser
from alfred.domain.events import ApplicationStarted, Event
from alfred.domain.hotkeys import HotkeyBackend
from alfred.domain.module import Module
from alfred.infrastructure.db.activity_repository import SqlActivityLogRepository
from alfred.infrastructure.db.database import DB_FILE, Database
from alfred.infrastructure.db.permission_repository import SqlPermissionRepository
from alfred.infrastructure.db.task_repository import SqlTaskRepository
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
        self.action_log: ActionLog | None = None
        self.input_lock: InputLock | None = None
        self.tools: ToolRegistry | None = None
        self.permissions: PermissionService | None = None
        self.orchestrator: Orchestrator | None = None
        self.memory: Any = None
        self.triggers: Any = None
        self.ai: AIService | None = None
        self.overlay: Module | None = None
        self.virtual_cursor: Module | None = None
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
            await self.bus.publish(ApplicationStarted())
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
    audio_backend: Any | None = None,
    app_lister: Any | None = None,
    ai_providers: tuple[Any, ...] = (),
    credentials: Any | None = None,
    wake_loader: Any | None = None,
    stt_factory: Any | None = None,
    tts_builder: Any | None = None,
    tts_output: Any | None = None,
    models_root: Path | None = None,
    with_ui: bool = False,
    system_observers: bool = False,
) -> tuple[Application, LoadResult]:
    """Monta a aplicação padrão a partir do diretório de dados.

    As configurações são carregadas de forma síncrona, antes do logging, para que o logging já
    nasça configurado; os avisos do carregamento vêm em ``LoadResult.warnings``.

    ``autostart``, ``hotkeys``, ``input_releaser`` e ``with_ui`` só são ligados pelo executável
    real, para que testes nunca toquem no registro do Windows, nos atalhos globais, no teclado e
    mouse nem na bandeja do sistema.
    """
    models_root = models_root or data_root / "models"
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
    action_log = ActionLog(activity)
    input_lock = InputLock()
    tool_registry = ToolRegistry()
    task_history = SqlTaskRepository(database)
    confirmations = ConfirmationService(bus, activity=activity)
    permissions = PermissionService(
        SqlPermissionRepository(database), confirmations, ActionLog(activity)
    )

    command_registry = CommandRegistry()

    async def emergency_by_voice() -> None:
        await emergency.trigger("voz")

    voice_handler = VoiceCommandHandler(
        bus, emergency=emergency_by_voice, registry=command_registry
    )

    ai_registry = ProviderRegistry()
    for provider in ai_providers:
        ai_registry.register(provider)
    if credentials is not None:
        from alfred.ai.openai_compat import OpenAICompatibleProvider

        ai_registry.register(
            OpenAICompatibleProvider(
                "externo",
                base_url=lambda: settings.current.ai.external_base_url,
                credentials=credentials,
                network_enabled=lambda: settings.current.privacy.network_enabled,
            )
        )
    ai = AIService(ai_registry, settings, bus)
    from alfred.domain.assistant import ListeningStarted

    warming: set[asyncio.Task[None]] = set()

    async def warm_ai(_: ListeningStarted) -> None:
        # Sem bloquear o bus: o modelo carrega em paralelo enquanto o usuário fala.
        task = asyncio.create_task(ai.warm_up(), name="ia:pre-carga")
        warming.add(task)
        task.add_done_callback(warming.discard)

    bus.subscribe(ListeningStarted, warm_ai, name="ai-warm-up")

    from alfred.domain.ai import ModelLoading
    from alfred.domain.voice import SpeakRequested

    async def announce_loading(event: ModelLoading) -> None:
        # Silêncio de vários segundos parece travamento: avisa o que está acontecendo.
        await bus.publish(
            SpeakRequested(text="Só um instante, estou carregando a inteligência artificial.")
        )

    bus.subscribe(ModelLoading, announce_loading, name="ai-loading-notice")

    modules: list[Module] = [
        ai,
        settings,
        database,
        emergency,
        confirmations,
        permissions,
        AssistantStateService(bus),
        TaskTracker(bus, task_history),
        voice_handler,
    ]
    if audio_backend is not None:
        from alfred.voice.capture import AudioCapture
        from alfred.voice.module import AudioModule
        from alfred.voice.wakeword_module import WakeWordModule, default_loader

        capture = AudioCapture(audio_backend)
        audio = AudioModule(capture, settings, bus)
        capture._on_device_change = audio.notify_device_change
        modules.append(audio)
        wake = WakeWordModule(capture, settings, bus, wake_loader or default_loader(models_root))
        modules.append(wake)
        from alfred.voice.stt import SpeechToText, SpeechToTextModule, faster_whisper_factory

        stt = SpeechToText(stt_factory or faster_whisper_factory(models_root))
        modules.append(SpeechToTextModule(stt, settings, bus))
        from alfred.voice.barge_in import BargeInModule
        from alfred.voice.tts import TtsModule, default_builder

        tts_kwargs = {"output_factory": tts_output} if tts_output is not None else {}
        tts = TtsModule(
            settings,
            bus,
            tts_builder or default_builder(models_root),
            devices=audio_backend,
            **tts_kwargs,
        )
        modules.append(tts)
        modules.append(BargeInModule(bus, tts, wake))
        from alfred.voice.pipeline import VoicePipeline

        app_voice_pipeline = VoicePipeline(bus, capture, stt)
        modules.append(app_voice_pipeline)
    if app_lister is not None:
        from alfred.automation.apps import AppCatalog
        from alfred.automation.service import AppsService
        from alfred.infrastructure.db.application_repository import SqlApplicationRepository

        apps = AppsService(
            AppCatalog(app_lister),
            action_log,
            confirmations,
            aliases=SqlApplicationRepository(database),
        )
        apps.register_commands(command_registry)
        modules.append(apps)
        from alfred.automation.window_service import WindowService

        windows = WindowService(action_log)
        windows.register_commands(command_registry)
        modules.append(windows)
        from alfred.automation.uia_service import UiaService

        uia = UiaService(action_log, cursor=lambda: _cursor_of(app))
        modules.append(uia)
        from alfred.automation.input_service import InputService

        keyboard = InputService(input_lock, action_log)
        modules.append(keyboard)
        from alfred.automation.browser import BrowserService

        browser = BrowserService(action_log, confirmations, data_root / "browser-profile")
        modules.append(browser)
        from alfred.automation.tools import register_automation_tools

        register_automation_tools(
            tool_registry, apps=apps, windows=windows, uia=uia, keyboard=keyboard, browser=browser
        )
        from alfred.automation.screen import ScreenCaptureService

        screen = ScreenCaptureService(settings, confirmations, action_log, bus)
        modules.append(screen)
        from alfred.ai.vision import ScreenAnalyzer, register_vision_tool

        async def capture_for(reason: str, window: str | None) -> bytes:
            if window:
                hwnd = (await windows.resolve(window)).hwnd
                return (await screen.capture(reason, target="window", hwnd=hwnd)).png
            return (await screen.capture(reason)).png

        register_vision_tool(
            tool_registry,
            ScreenAnalyzer(ai_registry, settings, confirmations, capture_for, uia.describe),
        )
    if autostart is not None:
        modules.append(AutostartService(autostart, settings, bus))
    hotkey_service: HotkeyService | None = None
    if hotkeys is not None:
        hotkey_service = HotkeyService(
            hotkeys, settings, bus, on_emergency=emergency.trigger_threadsafe
        )
        modules.append(hotkey_service)
    from alfred.infrastructure.db.memory_repository import SqlMemoryRepository
    from alfred.memory.service import MemoryService

    memory = MemoryService(SqlMemoryRepository(database), bus, config=lambda: settings.current.ai)
    from alfred.core.commands import CommandResult

    async def new_conversation(_: Any) -> CommandResult:
        await memory.new_conversation()
        return CommandResult("Certo, vamos começar uma conversa nova.", tool="nova_conversa")

    command_registry.register(
        r"(?:alfred\s+)?(?:nova conversa|vamos mudar de assunto|esquece (?:essa|a) conversa)",
        new_conversation,
    )
    modules.append(memory)
    from alfred.core.scheduler import StepScheduler

    orchestrator = Orchestrator(
        ai,
        tool_registry,
        permissions,
        tasks,
        bus,
        context_provider=memory.context,
        history_provider=memory.history,
        scheduler=StepScheduler(
            input_lock, max_parallel=lambda: settings.current.ai.max_parallel_steps
        ),
    )
    modules.append(orchestrator)
    voice_handler.fallback = lambda text: orchestrator.submit(text, origin="voice")
    from alfred.infrastructure.db.trigger_repository import SqlTriggerRepository
    from alfred.triggers.engine import TriggerEngine

    # Observação real de processos/bateria só no executável (testes não leem o sistema).
    trigger_observers: dict[str, Any] = (
        {} if system_observers else {"processes": None, "power": None}
    )
    trigger_engine = TriggerEngine(
        SqlTriggerRepository(database),
        bus,
        lambda request, task_id: orchestrator.submit(request, origin="trigger", task_id=task_id),
        commands=command_registry,
        hotkeys=hotkey_service,
        **trigger_observers,
    )
    modules.append(trigger_engine)
    app = create_application(modules, bus=bus)
    app.orchestrator = orchestrator
    app.memory = memory
    app.triggers = trigger_engine
    app.settings = settings
    app.database = database
    app.tasks = tasks
    app.emergency = emergency
    app.confirmations = confirmations
    app.action_log = action_log
    app.input_lock = input_lock
    app.tools = tool_registry
    app.permissions = permissions
    app.ai = ai
    if with_ui:
        from alfred.ui.activity_panel import ActivityPanelModule
        from alfred.ui.confirmation_panel import ConfirmationUiModule
        from alfred.ui.overlay import OverlayModule
        from alfred.ui.settings_window import SettingsWindowModule
        from alfred.ui.tray import TrayModule
        from alfred.ui.virtual_cursor import VirtualCursorModule
        from alfred.ui.widget import WidgetModule

        panel = ActivityPanelModule(
            bus=bus,
            tasks=task_history,
            activity=activity,
            is_running=tasks.is_running,
            cancel=tasks.cancel,
            export=lambda: action_log.export(data_root / "exports"),
        )
        app.manager.register(panel)
        app.manager.register(WidgetModule(bus=bus, settings=settings, on_click=panel.toggle))
        app.manager.register(ConfirmationUiModule(bus=bus, service=confirmations))
        overlay = OverlayModule()
        app.overlay = overlay
        app.manager.register(overlay)
        from alfred.domain.screen import ScreenCaptured

        async def flash_capture(event: ScreenCaptured) -> None:
            # Indicação visual de toda captura de tela (RF-008).
            if overlay.annotator is not None:
                overlay.annotator.highlight(event.area, label="Captura de tela", duration=0.8)

        bus.subscribe(ScreenCaptured, flash_capture, name="capture-flash")
        app.virtual_cursor = VirtualCursorModule(overlay=overlay, settings=settings, bus=bus)
        app.manager.register(app.virtual_cursor)
        settings_window = SettingsWindowModule(
            bus=bus,
            settings=settings,
            on_open_panel=panel.show,
            memory=lambda: app.memory,
            triggers=lambda: app.triggers,
            ai_providers=lambda: provider_locality(ai_registry),
        )
        app.manager.register(settings_window)
        app.manager.register(
            TrayModule(
                bus=bus,
                settings=settings,
                data_dir=data_root,
                on_quit=app.request_shutdown,
                on_open_panel=panel.show,
                on_open_settings=settings_window.show,
                voice_available=audio_backend is not None,
            )
        )
    return app, loaded


def provider_locality(registry: ProviderRegistry) -> dict[str, bool]:
    """Nome do provedor -> é local? (seção Privacidade)."""
    from alfred.ai.vision import is_local

    return {name: is_local(registry.get(name)) for name in registry.names}


def _cursor_of(app: Application | None) -> Any:
    """Cursor virtual da interface, quando existir (resolvido na hora do uso)."""
    module = getattr(app, "virtual_cursor", None) if app is not None else None
    return getattr(module, "cursor", None)


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
