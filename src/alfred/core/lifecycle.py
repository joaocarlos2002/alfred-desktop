"""Registro, ordenação e ciclo de vida (start/stop/health) dos módulos."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from enum import StrEnum

from alfred.domain.module import Health, HealthStatus, Module

logger = logging.getLogger(__name__)


class ModuleState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    SKIPPED = "skipped"
    STOPPED = "stopped"


class LifecycleError(Exception):
    """Erro de configuração ou execução do ciclo de vida."""


class DependencyError(LifecycleError):
    """Dependência ausente, duplicada ou circular entre módulos."""


class StartupError(LifecycleError):
    """Um módulo obrigatório não pôde ser iniciado."""

    def __init__(self, module: str, cause: BaseException) -> None:
        super().__init__(f"Falha ao iniciar o módulo obrigatório '{module}': {cause!r}")
        self.module = module


class ModuleManager:
    """Inicia os módulos em ordem de dependência e os encerra em ordem inversa."""

    def __init__(
        self,
        *,
        start_timeout: float = 10.0,
        stop_timeout: float = 5.0,
        health_timeout: float = 2.0,
    ) -> None:
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._health_timeout = health_timeout
        self._modules: dict[str, Module] = {}
        self._states: dict[str, ModuleState] = {}
        self._running: list[str] = []

    def register(self, module: Module) -> None:
        name = module.name
        if name in self._modules:
            raise DependencyError(f"Módulo duplicado: '{name}'")
        self._modules[name] = module
        self._states[name] = ModuleState.PENDING

    @property
    def states(self) -> Mapping[str, ModuleState]:
        return dict(self._states)

    def resolve_order(self) -> list[str]:
        """Ordem topológica estável (respeita a ordem de registro entre independentes)."""
        for name, module in self._modules.items():
            for dep in module.depends_on:
                if dep not in self._modules:
                    raise DependencyError(f"'{name}' depende de '{dep}', que não está registrado")

        order: list[str] = []
        done: set[str] = set()
        path: list[str] = []

        def visit(name: str) -> None:
            if name in done:
                return
            if name in path:
                cycle = [*path[path.index(name) :], name]
                raise DependencyError("Dependência circular: " + " -> ".join(cycle))
            path.append(name)
            for dep in self._modules[name].depends_on:
                visit(dep)
            path.pop()
            done.add(name)
            order.append(name)

        for name in self._modules:
            visit(name)
        return order

    async def start_all(self) -> None:
        """Inicia todos os módulos.

        Raises:
            DependencyError: grafo de dependências inválido (nada é iniciado).
            StartupError: um módulo obrigatório falhou; os já iniciados são encerrados.
        """
        for name in self.resolve_order():
            module = self._modules[name]
            unavailable = [
                dep for dep in module.depends_on if self._states[dep] is not ModuleState.RUNNING
            ]
            if unavailable:
                cause = DependencyError(f"dependências indisponíveis: {', '.join(unavailable)}")
                if module.optional:
                    self._states[name] = ModuleState.SKIPPED
                    logger.warning("Módulo opcional '%s' ignorado: %s", name, cause)
                    continue
                self._states[name] = ModuleState.FAILED
                await self.stop_all()
                raise StartupError(name, cause)

            started_at = time.perf_counter()
            try:
                await asyncio.wait_for(module.start(), self._start_timeout)
            except Exception as exc:
                self._states[name] = ModuleState.FAILED
                if module.optional:
                    logger.warning(
                        "Falha ao iniciar o módulo opcional '%s'; seguindo sem ele",
                        name,
                        exc_info=exc,
                    )
                    continue
                logger.error("Falha ao iniciar o módulo obrigatório '%s'", name, exc_info=exc)
                await self.stop_all()
                raise StartupError(name, exc) from exc

            self._states[name] = ModuleState.RUNNING
            self._running.append(name)
            logger.info(
                "Módulo '%s' iniciado em %.0f ms",
                name,
                (time.perf_counter() - started_at) * 1000,
            )

    async def stop_all(self) -> None:
        """Encerra os módulos em execução, em ordem inversa. Nunca propaga exceções."""
        while self._running:
            name = self._running.pop()
            try:
                await asyncio.wait_for(self._modules[name].stop(), self._stop_timeout)
            except TimeoutError:
                self._states[name] = ModuleState.FAILED
                logger.error(
                    "Módulo '%s' excedeu o tempo de encerramento (%.1f s)",
                    name,
                    self._stop_timeout,
                )
            except Exception:
                self._states[name] = ModuleState.FAILED
                logger.exception("Falha ao encerrar o módulo '%s'", name)
            else:
                self._states[name] = ModuleState.STOPPED
                logger.info("Módulo '%s' encerrado", name)

    async def health(self) -> dict[str, Health]:
        report: dict[str, Health] = {}
        for name, state in self._states.items():
            if state is not ModuleState.RUNNING:
                report[name] = Health(status=HealthStatus.UNHEALTHY, detail=f"estado: {state}")
                continue
            try:
                report[name] = await asyncio.wait_for(
                    self._modules[name].health(), self._health_timeout
                )
            except Exception as exc:
                report[name] = Health(status=HealthStatus.UNHEALTHY, detail=repr(exc))
        return report
