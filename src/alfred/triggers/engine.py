"""Motor de gatilhos (RF-011): frase de voz, atalho, horário, inicialização, abertura de
aplicativo e eventos do sistema.

Ao disparar, o gatilho entrega seu pedido ao orquestrador (origem ``"trigger"``) — a execução
passa pelas mesmas ferramentas, permissões e confirmações de um pedido falado. Cada disparo fica
registrado em ``trigger_executions`` com o resultado da tarefa.

Abertura de aplicativos e eventos de energia são observados por consulta periódica leve (nomes de
processos e estado da bateria), e só enquanto existir algum gatilho habilitado desses tipos.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from alfred.core.commands import CommandRegistry, CommandResult
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.voice_commands import normalize
from alfred.domain.module import Health, HealthStatus, Module
from alfred.domain.tasks import TaskFinished
from alfred.domain.triggers import SYSTEM_EVENTS, TriggerFired, TriggerSkipped, TriggerSpec
from alfred.infrastructure.db.trigger_repository import ExecutionRecord
from alfred.triggers.cron import Cron, CronError

logger = logging.getLogger(__name__)

POLL_SECONDS = 3.0
DEFAULT_BATTERY_LIMIT = 20


class TriggerStore(Protocol):
    async def list(self) -> list[TriggerSpec]: ...

    async def save(self, spec: TriggerSpec) -> TriggerSpec: ...

    async def delete(self, trigger_id: int) -> bool: ...

    async def start_execution(self, trigger_id: int) -> int: ...

    async def finish_execution(
        self,
        execution_id: int,
        status: str,
        *,
        error: str | None = None,
        task_id: UUID | None = None,
    ) -> None: ...

    async def mark_interrupted(self) -> int: ...

    async def executions(
        self, trigger_id: int | None = None, *, limit: int = 50
    ) -> builtins.list[ExecutionRecord]: ...


class Hotkeys(Protocol):
    async def register_extra(self, hotkey: str, callback: Callable[[], None]) -> int: ...

    async def unregister_extra(self, hotkey_id: int) -> None: ...


@dataclass(frozen=True)
class PowerState:
    plugged: bool
    percent: float


type Submit = Callable[[str, UUID], object]
"""(pedido, task_id) -> entrega ao orquestrador."""


def running_processes() -> set[str]:
    import psutil

    names = set()
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name")
        if name:
            names.add(str(name).casefold())
    return names


def power_state() -> PowerState | None:
    import psutil

    battery = psutil.sensors_battery()
    if battery is None:  # desktop sem bateria
        return None
    return PowerState(plugged=bool(battery.power_plugged), percent=float(battery.percent))


def validate(spec: TriggerSpec) -> None:
    """Confere a configuração da fonte. Raises: ValueError com mensagem para o usuário."""
    value = spec.source_value().strip()
    if spec.kind == "startup":
        return
    if not value:
        raise ValueError("Informe a configuração da fonte do gatilho")
    if spec.kind == "schedule":
        try:
            Cron.parse(value)
        except CronError as exc:
            raise ValueError(str(exc)) from exc
    if spec.kind == "system" and value not in SYSTEM_EVENTS:
        raise ValueError(f"Evento do sistema desconhecido: {value}")


def _app_key(name: str) -> str:
    name = name.casefold().strip()
    return name if name.endswith(".exe") else f"{name}.exe"


class TriggerEngine(Module):
    name = "triggers"
    depends_on = ("database", "event_bus")
    optional = True

    def __init__(
        self,
        store: TriggerStore,
        bus: EventBus,
        submit: Submit,
        *,
        commands: CommandRegistry | None = None,
        hotkeys: Hotkeys | None = None,
        processes: Callable[[], set[str]] | None = running_processes,
        power: Callable[[], PowerState | None] | None = power_state,
        clock: Callable[[], datetime] = datetime.now,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._store = store
        self._bus = bus
        self._submit = submit
        self._commands = commands
        self._hotkeys = hotkeys
        self._processes = processes
        self._power = power
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._triggers: dict[int, TriggerSpec] = {}
        self._running: dict[UUID, tuple[int, int]] = {}  # task_id -> (trigger_id, execution_id)
        self._unregister: list[Callable[[], Any]] = []
        self._hotkey_ids: list[int] = []
        self._loops: list[asyncio.Task[None]] = []
        self._subscription: Subscription[TaskFinished] | None = None
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._known_processes: set[str] | None = None
        self._last_power: PowerState | None = None
        self.problems: dict[int, str] = {}
        """Gatilhos que não puderam ser armados (ex.: atalho em uso) -> motivo."""

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        interrupted = await self._store.mark_interrupted()
        if interrupted:
            logger.warning("%d execução(ões) de gatilho interrompidas antes", interrupted)
        self._subscription = self._bus.subscribe(TaskFinished, self._on_finished, name="triggers")
        await self.reload()
        self._loops = [
            asyncio.create_task(self._schedule_loop(), name="gatilhos:horario"),
            asyncio.create_task(self._poll_loop(), name="gatilhos:observacao"),
        ]
        for spec in self._enabled("startup"):
            await self.fire(spec, detail="inicialização")

    async def stop(self) -> None:
        for task in self._loops:
            task.cancel()
        for task in self._loops:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._loops.clear()
        if self._subscription is not None:
            self._subscription.unsubscribe()
        await self._disarm()

    async def health(self) -> Health:
        if self.problems:
            names = ", ".join(self._triggers[i].name for i in self.problems if i in self._triggers)
            return Health(status=HealthStatus.DEGRADED, detail=f"gatilhos inativos: {names}")
        return Health(status=HealthStatus.HEALTHY, detail=f"{len(self._triggers)} gatilho(s)")

    # ------------------------------------------------------------------ CRUD
    async def triggers(self) -> list[TriggerSpec]:
        return await self._store.list()

    async def save(self, spec: TriggerSpec) -> TriggerSpec:
        validate(spec)
        saved = await self._store.save(spec)
        await self.reload()
        return saved

    async def delete(self, trigger_id: int) -> bool:
        deleted = await self._store.delete(trigger_id)
        await self.reload()
        return deleted

    async def set_enabled(self, trigger_id: int, enabled: bool) -> TriggerSpec:
        current = next(t for t in await self._store.list() if t.id == trigger_id)
        return await self.save(current.model_copy(update={"enabled": enabled}))

    async def executions(self, trigger_id: int | None = None) -> list[ExecutionRecord]:
        return await self._store.executions(trigger_id)

    # ------------------------------------------------------------------ armar fontes
    def _enabled(self, kind: str) -> list[TriggerSpec]:
        return [t for t in self._triggers.values() if t.enabled and t.kind == kind]

    async def reload(self) -> None:
        async with self._lock:
            await self._disarm()
            self._triggers = {t.id: t for t in await self._store.list() if t.id is not None}
            self.problems = {}
            for spec in self._triggers.values():
                if spec.enabled:
                    await self._arm(spec)
            if not self._enabled("app_open"):
                self._known_processes = None

    async def _arm(self, spec: TriggerSpec) -> None:
        assert spec.id is not None
        trigger_id = spec.id
        if spec.kind == "voice" and self._commands is not None:
            phrase = normalize(spec.source_value())

            async def by_voice(_: re.Match[str]) -> CommandResult:
                fired = await self._fire_id(trigger_id, "frase de voz")
                reply = f"Gatilho {spec.name} acionado." if fired else "Agora não posso fazer isso."
                return CommandResult(reply, succeeded=fired, tool="gatilho")

            pattern = re.compile(r"(?:alfred\s+)?" + re.escape(phrase), re.IGNORECASE)
            self._unregister.append(self._commands.register(pattern, by_voice))
        elif spec.kind == "hotkey":
            if self._hotkeys is None:
                self.problems[trigger_id] = "atalhos globais indisponíveis"
                return
            try:
                hotkey_id = await self._hotkeys.register_extra(
                    spec.source_value(), lambda: self._fire_threadsafe(trigger_id)
                )
            except Exception as exc:  # atalho inválido ou já usado por outro programa
                self.problems[trigger_id] = str(exc) or type(exc).__name__
                logger.warning("Gatilho '%s': atalho não registrado (%s)", spec.name, exc)
                return
            self._hotkey_ids.append(hotkey_id)

    async def _disarm(self) -> None:
        for remove in self._unregister:
            remove()
        self._unregister.clear()
        if self._hotkeys is not None:
            for hotkey_id in self._hotkey_ids:
                await self._hotkeys.unregister_extra(hotkey_id)
        self._hotkey_ids.clear()

    def _fire_threadsafe(self, trigger_id: int) -> None:
        """Chamado na thread do backend de atalhos."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._fire_id(trigger_id, "atalho"))
        )

    async def _fire_id(self, trigger_id: int, detail: str) -> bool:
        spec = self._triggers.get(trigger_id)
        return spec is not None and await self.fire(spec, detail=detail)

    # ------------------------------------------------------------------ disparo
    async def fire(self, spec: TriggerSpec, *, detail: str = "") -> bool:
        assert spec.id is not None
        if not spec.enabled:
            return await self._skip(spec, "gatilho desabilitado")
        if not spec.condition.allows(self._clock()):
            return await self._skip(spec, "fora da condição de horário/dia")
        if any(tid == spec.id for tid, _ in self._running.values()):
            return await self._skip(spec, "execução anterior ainda em andamento")
        execution_id = await self._store.start_execution(spec.id)
        task_id = uuid4()
        self._running[task_id] = (spec.id, execution_id)
        await self._bus.publish(
            TriggerFired(trigger_id=spec.id, name=spec.name, kind=spec.kind, detail=detail)
        )
        logger.info("Gatilho '%s' disparou (%s)", spec.name, detail or spec.kind)
        try:
            self._submit(spec.request, task_id)
        except Exception as exc:
            self._running.pop(task_id, None)
            await self._store.finish_execution(execution_id, "failed", error=str(exc))
            raise
        return True

    async def _skip(self, spec: TriggerSpec, reason: str) -> bool:
        assert spec.id is not None
        logger.info("Gatilho '%s' não disparou: %s", spec.name, reason)
        await self._bus.publish(TriggerSkipped(trigger_id=spec.id, name=spec.name, reason=reason))
        return False

    async def _on_finished(self, event: TaskFinished) -> None:
        found = self._running.pop(event.task_id, None)
        if found is None:
            return
        _, execution_id = found
        await self._store.finish_execution(
            execution_id, event.status.value, error=event.error, task_id=event.task_id
        )

    # ------------------------------------------------------------------ horário
    async def _schedule_loop(self) -> None:
        while True:
            now = self._clock()
            following = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            await asyncio.sleep(max(0.05, (following - now).total_seconds()))
            await self.check_schedule(following)

    async def check_schedule(self, moment: datetime) -> None:
        for spec in self._enabled("schedule"):
            try:
                cron = Cron.parse(spec.source_value())
            except CronError:
                continue
            if cron.matches(moment):
                await self.fire(spec, detail=f"horário {moment:%H:%M}")

    # ------------------------------------------------------------------ observação
    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self.poll()
            except Exception:
                logger.exception("Falha ao observar aplicativos/eventos do sistema")

    async def poll(self) -> None:
        if self._enabled("app_open") and self._processes is not None:
            current = await asyncio.to_thread(self._processes)
            known, self._known_processes = self._known_processes, current
            if known is not None:  # a primeira leitura é só a referência
                opened = current - known
                for spec in self._enabled("app_open"):
                    if _app_key(spec.source_value()) in opened:
                        await self.fire(spec, detail=f"{spec.source_value()} aberto")
        if self._enabled("system") and self._power is not None:
            state = await asyncio.to_thread(self._power)
            previous, self._last_power = self._last_power, state
            if state is None or previous is None:
                return
            for spec in self._enabled("system"):
                event = spec.source_value()
                if event == "energia_conectada" and state.plugged and not previous.plugged:
                    await self.fire(spec, detail="carregador conectado")
                elif event == "energia_desconectada" and previous.plugged and not state.plugged:
                    await self.fire(spec, detail="carregador desconectado")
                elif event == "bateria_baixa":
                    limit = float(spec.config.get("limite", DEFAULT_BATTERY_LIMIT))
                    if not state.plugged and previous.percent > limit >= state.percent:
                        await self.fire(spec, detail=f"bateria em {state.percent:.0f}%")
