"""Execução de etapas com grafo de dependências e paralelismo controlado (RF-010).

Cada etapa pode declarar um *recurso* compartilhado. Etapas do mesmo recurso rodam uma de cada vez
e na ordem proposta; as do recurso ``"input"`` (foco, teclado e mouse) ainda seguram a
``InputLock`` global durante toda a etapa, de modo que nenhuma outra tarefa do ALFRED mexa no input
no meio delas. Etapas sem recurso (rede, arquivos, leitura) rodam em paralelo, até
``max_parallel`` ao mesmo tempo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from alfred.core.input_lock import InputLock

R = TypeVar("R")

INPUT = "input"


@dataclass
class Step:
    key: int
    name: str
    resource: str | None = None
    depends_on: set[int] = field(default_factory=set)


def plan_batch(steps: Sequence[Step]) -> list[Step]:
    """Dependências implícitas de um lote de chamadas propostas juntas pelo modelo.

    - mesmo recurso: cada etapa espera a anterior daquele recurso (ordem preservada);
    - sem recurso: espera todas as etapas com recurso que vieram antes dela (ex.: ler a janela
      só depois de abri-la), mas roda em paralelo com as demais sem recurso.
    """
    last_by_resource: dict[str, int] = {}
    with_resource: list[int] = []
    for step in steps:
        if step.resource is not None:
            previous = last_by_resource.get(step.resource)
            if previous is not None:
                step.depends_on.add(previous)
            last_by_resource[step.resource] = step.key
            with_resource.append(step.key)
        else:
            step.depends_on.update(with_resource)
    return list(steps)


class StepScheduler:
    def __init__(
        self, input_lock: InputLock | None = None, *, max_parallel: Callable[[], int] = lambda: 4
    ) -> None:
        self._input_lock = input_lock
        self._max_parallel = max_parallel
        self._resource_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _check(steps: Sequence[Step]) -> None:
        keys = {s.key for s in steps}
        if len(keys) != len(steps):
            raise ValueError("Etapas com chave repetida")
        graph = {s.key: s.depends_on for s in steps}
        for step in steps:
            missing = step.depends_on - keys
            if missing:
                raise ValueError(f"Etapa {step.key} depende de etapas inexistentes: {missing}")
        visiting: set[int] = set()
        done: set[int] = set()

        def visit(key: int) -> None:
            if key in done:
                return
            if key in visiting:
                raise ValueError(f"Ciclo de dependências envolvendo a etapa {key}")
            visiting.add(key)
            for dep in graph[key]:
                visit(dep)
            visiting.discard(key)
            done.add(key)

        for key in graph:
            visit(key)

    async def run(
        self, steps: Sequence[Step], runner: Callable[[Step], Awaitable[R]]
    ) -> dict[int, R]:
        """Executa todas as etapas; o cancelamento de uma cancela o lote inteiro."""
        self._check(steps)
        finished = {s.key: asyncio.Event() for s in steps}
        results: dict[int, R] = {}
        limit = asyncio.Semaphore(max(1, self._max_parallel()))

        async def execute(step: Step) -> None:
            for dep in step.depends_on:
                await finished[dep].wait()
            async with limit:
                if step.resource is None:
                    results[step.key] = await runner(step)
                else:
                    lock = self._resource_locks.setdefault(step.resource, asyncio.Lock())
                    async with lock:
                        if step.resource == INPUT and self._input_lock is not None:
                            async with self._input_lock.hold(step.name):
                                results[step.key] = await runner(step)
                        else:
                            results[step.key] = await runner(step)
            finished[step.key].set()

        try:
            async with asyncio.TaskGroup() as group:
                for step in steps:
                    group.create_task(execute(step), name=f"etapa:{step.key}:{step.name}")
        except BaseExceptionGroup as errors:  # mantém o tipo original para quem chamou
            raise errors.exceptions[0] from None
        return results
