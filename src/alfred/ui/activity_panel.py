"""Painel de atividades: tarefas, etapas e histórico reais (RF-002, RF-014).

Nada aqui é estimado: o progresso é a contagem de etapas concluídas, e cada linha vem do banco,
alimentado pelos eventos reais do orquestrador e pelo histórico de atividades.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.activity import ActivityEntry, ActivityLogRepository
from alfred.domain.confirmation import ConfirmationResolved
from alfred.domain.emergency import EmergencyStopped
from alfred.domain.events import Event
from alfred.domain.module import Module
from alfred.domain.task_history import StepRecord, TaskRecord, TaskRepository
from alfred.domain.tasks import (
    StepStatus,
    TaskFinished,
    TaskStarted,
    TaskStatus,
    TaskStepFinished,
    TaskStepStarted,
)
from alfred.ui.assets import avatar_pixmap, butler_pixmap
from alfred.ui.icon import alfred_icon
from alfred.ui.theme import THEME

logger = logging.getLogger(__name__)

PERIODS: dict[str, timedelta | None] = {
    "Últimas 24 horas": timedelta(days=1),
    "Última hora": timedelta(hours=1),
    "Últimos 7 dias": timedelta(days=7),
    "Tudo": None,
}
STATUS_FILTERS: dict[str, TaskStatus | None] = {
    "Todos os status": None,
    "Em execução": TaskStatus.RUNNING,
    "Concluídas": TaskStatus.SUCCEEDED,
    "Com falha": TaskStatus.FAILED,
    "Canceladas": TaskStatus.CANCELLED,
}
STATUS_LABELS = {
    "running": "Em execução",
    "succeeded": "Concluída",
    "failed": "Falhou",
    "cancelled": "Cancelada",
    "denied": "Recusada",
    "started": "Iniciada",
}
STATUS_COLOR = {
    "running": "executing",
    "succeeded": "success",
    "failed": "error",
    "denied": "confirm",
    "cancelled": "text_muted",
    "started": "executing",
}
REFRESH_EVENTS = (
    TaskStarted,
    TaskStepStarted,
    TaskStepFinished,
    TaskFinished,
    ConfirmationResolved,
    EmergencyStopped,
)
TASK_ID_ROLE = Qt.ItemDataRole.UserRole
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 1


def format_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None:
        return ""
    seconds = ((end or datetime.now(UTC)) - start).total_seconds()
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{int(seconds // 60)} min {int(seconds % 60)} s"


def format_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%d/%m %H:%M:%S")


def _status_item(item: QTreeWidgetItem, column: int, status: str) -> None:
    item.setText(column, STATUS_LABELS.get(status, status))
    item.setForeground(column, THEME.color(STATUS_COLOR.get(status, "text")))


def _pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def task_detail(task: TaskRecord) -> str:
    lines = [
        f"Tarefa: {task.title}",
        f"Status: {STATUS_LABELS.get(task.status, task.status)}",
        f"Origem: {task.origin}",
        f"Correlation ID: {task.correlation_id}",
        f"Etapas concluídas: {task.completed_steps} de {len(task.steps)} registradas",
    ]
    if task.error:
        lines.append(f"\nErro: {task.error}")
    return "\n".join(lines)


def step_detail(step: StepRecord) -> str:
    lines = [
        f"Etapa {step.position}: {step.tool}",
        f"Status: {STATUS_LABELS.get(step.status, step.status)}",
        f"Duração: {format_duration(step.started_at, step.finished_at) or '—'}",
        f"\nParâmetros:\n{_pretty(step.params)}",
    ]
    if step.result is not None:
        lines.append(f"\nResultado:\n{_pretty(step.result)}")
    if step.error:
        lines.append(f"\nErro: {step.error}")
    return "\n".join(lines)


class EmptyState(QWidget):
    """Estado vazio com a ilustração do ALFRED: nunca exibe dados de exemplo."""

    def __init__(self, message: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addStretch()
        illustration = QLabel()
        illustration.setPixmap(butler_pixmap(150))
        illustration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message = QLabel(message)
        self.message.setProperty("role", "muted")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(illustration)
        layout.addWidget(self.message)
        layout.addStretch()


class ActivityPanel(QWidget):
    def __init__(self, *, on_cancel: Callable[[UUID], bool]) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("ALFRED — Atividades")
        self.setWindowIcon(alfred_icon())
        self.resize(820, 540)
        self._on_cancel = on_cancel
        self._running_ids: set[UUID] = set()
        s = THEME.spacing

        layout = QVBoxLayout(self)
        layout.setContentsMargins(s.lg, s.lg, s.lg, s.lg)
        header = QHBoxLayout()
        avatar = QLabel()
        avatar.setPixmap(avatar_pixmap(44))
        title = QLabel("Atividades")
        title.setProperty("role", "title")
        subtitle = QLabel("Tudo o que o ALFRED fez, com resultados reais.")
        subtitle.setProperty("role", "muted")
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addWidget(avatar)
        header.addSpacing(s.sm)
        header.addLayout(titles)
        header.addStretch()
        layout.addLayout(header)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # ---------------------------------------------------------------- tarefas
        tasks_tab = QWidget()
        tasks_layout = QVBoxLayout(tasks_tab)
        filters = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItems(list(STATUS_FILTERS))
        self.period_filter = QComboBox()
        self.period_filter.addItems(list(PERIODS))
        self.cancel_button = QPushButton("Cancelar tarefa")
        self.cancel_button.setProperty("role", "danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_selected)
        filters.addWidget(self.status_filter)
        filters.addWidget(self.period_filter)
        filters.addStretch()
        filters.addWidget(self.cancel_button)
        tasks_layout.addLayout(filters)

        self.tasks_tree = QTreeWidget()
        self.tasks_tree.setHeaderLabels(
            ["Tarefa", "Status", "Origem", "Início", "Duração", "Etapas concluídas"]
        )
        self.tasks_tree.setColumnWidth(0, 260)
        self.tasks_tree.currentItemChanged.connect(self._on_selection)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Selecione uma tarefa ou etapa para ver os detalhes.")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tasks_tree)
        splitter.addWidget(self.detail)
        splitter.setSizes([340, 140])
        self.tasks_empty = EmptyState("Nenhuma tarefa registrada neste período.")
        self.tasks_stack = QStackedWidget()
        self.tasks_view = splitter
        self.tasks_stack.addWidget(splitter)
        self.tasks_stack.addWidget(self.tasks_empty)
        tasks_layout.addWidget(self.tasks_stack)
        self.feedback = QLabel()
        self.feedback.setProperty("role", "muted")
        tasks_layout.addWidget(self.feedback)
        self.tabs.addTab(tasks_tab, "Tarefas")

        # ---------------------------------------------------------------- histórico
        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        self.history_tree = QTreeWidget()
        self.history_tree.setHeaderLabels(["Quando", "Categoria", "Ação", "Status", "Detalhes"])
        self.history_tree.setColumnWidth(0, 130)
        self.history_tree.setColumnWidth(2, 160)
        self.history_empty = EmptyState("Nenhuma atividade registrada neste período.")
        self.history_stack = QStackedWidget()
        self.history_stack.addWidget(self.history_tree)
        self.history_stack.addWidget(self.history_empty)
        history_layout.addWidget(self.history_stack)
        self.tabs.addTab(history_tab, "Histórico")

    # ------------------------------------------------------------------ filtros
    @property
    def selected_status(self) -> TaskStatus | None:
        return STATUS_FILTERS[self.status_filter.currentText()]

    @property
    def since(self) -> datetime | None:
        period = PERIODS[self.period_filter.currentText()]
        return datetime.now(UTC) - period if period else None

    # ------------------------------------------------------------------ dados
    def set_tasks(self, tasks: list[TaskRecord], running: set[UUID]) -> None:
        selected = self._selected_task_id()
        self._running_ids = running
        self.tasks_tree.clear()
        for task in tasks:
            item = QTreeWidgetItem(self.tasks_tree)
            item.setText(0, task.title)
            _status_item(item, 1, task.status)
            item.setText(2, task.origin)
            item.setText(3, format_time(task.created_at))
            item.setText(4, format_duration(task.created_at, task.finished_at))
            item.setText(5, str(task.completed_steps))
            item.setData(0, TASK_ID_ROLE, str(task.id))
            item.setData(0, DETAIL_ROLE, task_detail(task))
            for step in task.steps:
                child = QTreeWidgetItem(item)
                child.setText(0, f"{step.position}. {step.tool}")
                _status_item(child, 1, step.status)
                child.setText(4, format_duration(step.started_at, step.finished_at))
                child.setData(0, TASK_ID_ROLE, str(task.id))
                child.setData(0, DETAIL_ROLE, step_detail(step))
                if step.status is StepStatus.FAILED and step.error:
                    child.setToolTip(0, step.error)
            if task.status is TaskStatus.RUNNING:
                item.setExpanded(True)
            if selected == task.id:
                self.tasks_tree.setCurrentItem(item)
        self.tasks_stack.setCurrentWidget(self.tasks_empty if not tasks else self.tasks_view)
        self._update_cancel()

    def set_history(self, entries: list[ActivityEntry]) -> None:
        self.history_tree.clear()
        for entry in entries:
            item = QTreeWidgetItem(self.history_tree)
            item.setText(0, format_time(entry.timestamp))
            item.setText(1, entry.category)
            item.setText(2, entry.action)
            _status_item(item, 3, entry.status)
            item.setText(4, json.dumps(entry.detail, ensure_ascii=False, default=str))
            item.setToolTip(4, _pretty(entry.detail))
        self.history_stack.setCurrentWidget(
            self.history_empty if not entries else self.history_tree
        )

    @property
    def showing_empty_tasks(self) -> bool:
        return self.tasks_stack.currentWidget() is self.tasks_empty

    # ------------------------------------------------------------------ seleção / cancelar
    def _selected_task_id(self) -> UUID | None:
        item = self.tasks_tree.currentItem()
        raw = item.data(0, TASK_ID_ROLE) if item is not None else None
        return UUID(raw) if raw else None

    def _on_selection(self, current: QTreeWidgetItem | None, _: Any) -> None:
        self.detail.setPlainText(current.data(0, DETAIL_ROLE) if current else "")
        self._update_cancel()

    def _update_cancel(self) -> None:
        task_id = self._selected_task_id()
        self.cancel_button.setEnabled(task_id is not None and task_id in self._running_ids)

    def _cancel_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            return
        if self._on_cancel(task_id):
            self.feedback.setText("Cancelamento solicitado.")
        else:
            self.feedback.setText("A tarefa não está mais em execução.")
        self._running_ids.discard(task_id)
        self._update_cancel()


class ActivityPanelModule(Module):
    name = "activity_panel"
    depends_on = ("event_bus", "task_tracker")
    optional = True

    def __init__(
        self,
        *,
        bus: EventBus,
        tasks: TaskRepository,
        activity: ActivityLogRepository,
        is_running: Callable[[UUID], bool],
        cancel: Callable[[UUID], bool],
    ) -> None:
        self._bus = bus
        self._tasks = tasks
        self._activity = activity
        self._is_running = is_running
        self._cancel = cancel
        self.panel: ActivityPanel | None = None
        self._subscription: Subscription[Event] | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        panel = ActivityPanel(on_cancel=self._cancel)
        panel.status_filter.currentIndexChanged.connect(lambda _: self.schedule_refresh())
        panel.period_filter.currentIndexChanged.connect(lambda _: self.schedule_refresh())
        self.panel = panel
        self._subscription = self._bus.subscribe(Event, self._on_event, name="activity-panel")

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        if self._refresh_task is not None:
            self._refresh_task.cancel()
        if self.panel is not None:
            self.panel.close()
            self.panel.deleteLater()
            self.panel = None

    def toggle(self) -> None:
        if self.panel is None:
            return
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.show()

    def show(self) -> None:
        if self.panel is None:
            return
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        self.schedule_refresh()

    def schedule_refresh(self) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.ensure_future(self.refresh())

    async def refresh(self) -> None:
        panel = self.panel
        if panel is None:
            return
        try:
            tasks = await self._tasks.list_tasks(status=panel.selected_status, since=panel.since)
            history = await self._activity.list(since=panel.since, limit=200)
        except Exception:
            logger.exception("Falha ao carregar o painel de atividades")
            return
        if self.panel is None:
            return
        running = {task.id for task in tasks if self._is_running(task.id)}
        self.panel.set_tasks(tasks, running)
        self.panel.set_history(history)

    async def _on_event(self, event: Event) -> None:
        if isinstance(event, REFRESH_EVENTS) and self.panel is not None and self.panel.isVisible():
            self.schedule_refresh()
