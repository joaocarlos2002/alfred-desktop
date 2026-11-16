"""Estado visível do assistente, derivado exclusivamente de eventos reais (RF-002, RF-014)."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from alfred.domain.confirmation import ConfirmationRequested, ConfirmationResolved
from alfred.domain.emergency import EmergencyStopped
from alfred.domain.events import Event
from alfred.domain.tasks import (
    TaskFinished,
    TaskStarted,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)


class AssistantState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    EXECUTING = "executing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DONE = "done"
    ERROR = "error"


STATE_LABELS = {
    AssistantState.IDLE: "Inativo",
    AssistantState.LISTENING: "Ouvindo",
    AssistantState.PROCESSING: "Processando",
    AssistantState.EXECUTING: "Executando",
    AssistantState.AWAITING_CONFIRMATION: "Aguardando confirmação",
    AssistantState.DONE: "Concluído",
    AssistantState.ERROR: "Erro",
}

TRANSIENT_STATES = frozenset({AssistantState.DONE, AssistantState.ERROR})


class ListeningStarted(Event):
    """Captura de voz iniciada (publicado pelo módulo de voz)."""


class ListeningStopped(Event):
    pass


class AssistantStateChanged(Event):
    state: AssistantState
    detail: str | None = None


class AssistantStateMachine:
    """Reduz eventos a um estado. Prioridade: confirmação > ouvindo > executando > processando."""

    def __init__(self) -> None:
        self.state = AssistantState.IDLE
        self.detail: str | None = None
        self._listening = False
        self._tasks: dict[UUID, str] = {}
        self._running_steps: dict[UUID, str] = {}
        self._confirmations: dict[UUID, str] = {}

    def apply(self, event: Event) -> AssistantState | None:
        """Aplica o evento; devolve o novo estado se ele mudou."""
        outcome: AssistantState | None = None
        match event:
            case ListeningStarted():
                self._listening = True
            case ListeningStopped():
                self._listening = False
            case TaskStarted(task_id=task_id, title=title):
                self._tasks[task_id] = title
            case TaskStepStarted(task_id=task_id, tool=tool):
                self._running_steps[task_id] = tool
            case TaskStepFinished(task_id=task_id):
                self._running_steps.pop(task_id, None)
            case TaskFinished(task_id=task_id, status=status, error=error):
                finished = self._tasks.pop(task_id) if task_id in self._tasks else None
                self._running_steps.pop(task_id, None)
                if status is TaskStatus.SUCCEEDED:
                    outcome = AssistantState.DONE
                    self.detail = finished
                elif status is TaskStatus.FAILED:
                    outcome = AssistantState.ERROR
                    self.detail = error or finished
                else:
                    outcome = AssistantState.IDLE
                    self.detail = None
            case ConfirmationRequested(request_id=request_id, action=action):
                self._confirmations[request_id] = action
            case ConfirmationResolved(request_id=request_id):
                self._confirmations.pop(request_id, None)
            case EmergencyStopped():
                self._reset()
                outcome = AssistantState.IDLE
            case _:
                return None
        return self._set(self._derive(outcome))

    def settle(self) -> AssistantState | None:
        """Encerra um estado transitório (concluído/erro) se não houver mais atividade."""
        if self.state in TRANSIENT_STATES:
            return self._set(self._derive(AssistantState.IDLE))
        return None

    def _derive(self, outcome: AssistantState | None) -> AssistantState:
        if self._confirmations:
            self.detail = next(iter(self._confirmations.values()))
            return AssistantState.AWAITING_CONFIRMATION
        if self._listening:
            self.detail = None
            return AssistantState.LISTENING
        if self._running_steps:
            self.detail = next(iter(self._running_steps.values()))
            return AssistantState.EXECUTING
        if self._tasks:
            self.detail = next(iter(self._tasks.values()))
            return AssistantState.PROCESSING
        if outcome is not None:
            return outcome
        if self.state in TRANSIENT_STATES:
            return self.state
        self.detail = None
        return AssistantState.IDLE

    def _set(self, state: AssistantState) -> AssistantState | None:
        if state is self.state:
            return None
        self.state = state
        return state

    def _reset(self) -> None:
        self._listening = False
        self._tasks.clear()
        self._running_steps.clear()
        self._confirmations.clear()
        self.detail = None
