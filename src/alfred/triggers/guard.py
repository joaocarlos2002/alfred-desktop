"""Proteções do motor de gatilhos contra loops e excesso de execuções (RF-011).

- **Limite de frequência** por gatilho (janela deslizante).
- **Detecção de ciclos**: um gatilho disparado enquanto (ou logo depois que) a tarefa de outro
  gatilho rodava é considerado *causado* por ela — o ALFRED pode ter aberto o aplicativo,
  pressionado o atalho ou mudado o estado do sistema. Se o próprio gatilho já está nessa cadeia de
  causas, é um ciclo e o disparo é bloqueado.
- **Profundidade máxima** da cadeia de gatilhos.
- **Circuit breaker**: após falhas consecutivas, o gatilho fica suspenso por um tempo.

Frases de voz, horários e a inicialização não são causados por tarefas: sempre começam uma cadeia
nova (mas continuam sujeitos ao limite de frequência e ao circuit breaker).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

CAUSABLE_KINDS = frozenset({"hotkey", "app_open", "system"})


@dataclass(frozen=True)
class GuardLimits:
    max_per_window: int = 5
    window_seconds: float = 600.0
    max_chain_depth: int = 3
    """Quantos gatilhos podem se encadear (A → B → C) antes do bloqueio."""
    causal_window_seconds: float = 15.0
    """Depois que a tarefa de um gatilho termina, por quanto tempo ela ainda pode ser a causa."""
    breaker_failures: int = 3
    breaker_cooldown_seconds: float = 1800.0


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str | None = None
    chain: tuple[int, ...] = ()
    """Gatilhos que causaram este disparo, do mais antigo ao mais recente."""


@dataclass
class _Lineage:
    trigger_id: int
    chain: tuple[int, ...]
    finished_at: float | None = None


@dataclass
class TriggerGuard:
    limits: GuardLimits = field(default_factory=GuardLimits)
    clock: Callable[[], float] = time.monotonic
    names: Callable[[int], str] = str

    def __post_init__(self) -> None:
        self._history: dict[int, deque[float]] = {}
        self._failures: dict[int, int] = {}
        self._open_until: dict[int, float] = {}
        self._lineages: dict[UUID, _Lineage] = {}

    # ------------------------------------------------------------------ decisão
    def check(self, trigger_id: int, kind: str) -> Verdict:
        now = self.clock()
        reopen = self._open_until.get(trigger_id)
        if reopen is not None:
            if now < reopen:
                minutes = max(1, round((reopen - now) / 60))
                return Verdict(
                    False,
                    f"suspenso após {self.limits.breaker_failures} falhas seguidas "
                    f"(volta em ~{minutes} min)",
                )
            del self._open_until[trigger_id]
            self._failures[trigger_id] = 0
        recent = self._recent(trigger_id, now)
        if len(recent) >= self.limits.max_per_window:
            window = round(self.limits.window_seconds / 60)
            return Verdict(
                False, f"limite de {self.limits.max_per_window} disparos em {window} min atingido"
            )
        chain = self._cause_chain(now) if kind in CAUSABLE_KINDS else ()
        if trigger_id in chain:
            loop = " → ".join(
                self.names(t) for t in (*chain[chain.index(trigger_id) :], trigger_id)
            )
            return Verdict(False, f"ciclo de gatilhos detectado: {loop}", chain)
        if len(chain) >= self.limits.max_chain_depth:
            return Verdict(
                False,
                f"cadeia de gatilhos longa demais (máximo {self.limits.max_chain_depth})",
                chain,
            )
        return Verdict(True, chain=chain)

    def _recent(self, trigger_id: int, now: float) -> deque[float]:
        recent = self._history.setdefault(trigger_id, deque())
        while recent and now - recent[0] > self.limits.window_seconds:
            recent.popleft()
        return recent

    def _cause_chain(self, now: float) -> tuple[int, ...]:
        """Cadeia mais longa entre as tarefas de gatilho ativas ou recém-terminadas."""
        best: tuple[int, ...] = ()
        for task_id, lineage in list(self._lineages.items()):
            if (
                lineage.finished_at is not None
                and now - lineage.finished_at > self.limits.causal_window_seconds
            ):
                del self._lineages[task_id]
                continue
            candidate = (*lineage.chain, lineage.trigger_id)
            if len(candidate) > len(best):
                best = candidate
        return best

    # ------------------------------------------------------------------ registro
    def started(self, task_id: UUID, trigger_id: int, chain: tuple[int, ...]) -> None:
        self._recent(trigger_id, self.clock()).append(self.clock())
        self._lineages[task_id] = _Lineage(trigger_id, chain)

    def finished(self, task_id: UUID, *, failed: bool) -> bool:
        """Registra o fim da tarefa. Devolve ``True`` se o circuit breaker acabou de abrir."""
        lineage = self._lineages.get(task_id)
        if lineage is None:
            return False
        lineage.finished_at = self.clock()
        trigger_id = lineage.trigger_id
        if not failed:
            self._failures[trigger_id] = 0
            return False
        self._failures[trigger_id] = self._failures.get(trigger_id, 0) + 1
        if self._failures[trigger_id] >= self.limits.breaker_failures:
            self._open_until[trigger_id] = self.clock() + self.limits.breaker_cooldown_seconds
            return True
        return False

    def restore_failures(self, trigger_id: int, consecutive: int) -> None:
        """Falhas seguidas lidas do histórico no início (o breaker sobrevive a reinícios)."""
        self._failures[trigger_id] = consecutive
        if consecutive >= self.limits.breaker_failures:
            self._open_until[trigger_id] = self.clock() + self.limits.breaker_cooldown_seconds

    def is_open(self, trigger_id: int) -> bool:
        reopen = self._open_until.get(trigger_id)
        return reopen is not None and self.clock() < reopen
