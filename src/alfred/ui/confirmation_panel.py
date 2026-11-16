"""Painel de confirmação de ações sensíveis (seção 6).

Seguro por padrão: o foco inicial fica em "Recusar", Esc recusa e, sem resposta, o serviço recusa
quando o prazo acaba. Pedidos simultâneos são exibidos um de cada vez.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any
from uuid import UUID

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from alfred.core.confirmations import ConfirmationService
from alfred.core.event_bus import EventBus, Subscription
from alfred.domain.confirmation import (
    ConfirmationDecision,
    ConfirmationRequested,
    ConfirmationResolved,
)
from alfred.domain.module import Module
from alfred.ui.assets import avatar_pixmap
from alfred.ui.theme import THEME

EDGE_MARGIN = 24


class ConfirmationPanel(QWidget):
    WIDTH = 380

    def __init__(self, service: ConfirmationService) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._service = service
        self._queue: deque[ConfirmationRequested] = deque()
        self.current: ConfirmationRequested | None = None
        self._deadline = 0.0

        s = THEME.spacing
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(
            f"ConfirmationPanel {{ background-color: {THEME.colors.graphite};"
            f" border: 1px solid {THEME.colors.confirm}; border-radius: {THEME.radius.md}px; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(s.lg, s.lg, s.lg, s.lg)
        layout.setSpacing(s.sm)

        heading = QLabel("Confirmação necessária")
        heading.setProperty("role", "title")
        self.action = QLabel()
        self.action.setWordWrap(True)
        self.action.setStyleSheet(f"font-weight: 600; color: {THEME.colors.confirm};")
        self.target = QLabel()
        self.target.setWordWrap(True)
        self.target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.impact = QLabel()
        self.impact.setWordWrap(True)
        self.impact.setProperty("role", "muted")
        self.countdown = QLabel()
        self.countdown.setProperty("role", "muted")
        self.queued = QLabel()
        self.queued.setProperty("role", "muted")

        self.deny = QPushButton("Recusar")
        self.deny.setProperty("role", "danger")
        self.approve = QPushButton("Permitir")
        self.approve.setProperty("role", "primary")
        self.approve.setAutoDefault(False)
        self.deny.clicked.connect(lambda: self._answer(ConfirmationDecision.DENIED))
        self.approve.clicked.connect(lambda: self._answer(ConfirmationDecision.APPROVED))
        buttons = QHBoxLayout()
        buttons.addWidget(self.countdown)
        buttons.addStretch()
        buttons.addWidget(self.deny)
        buttons.addWidget(self.approve)

        avatar = QLabel()
        avatar.setPixmap(avatar_pixmap(36))
        header = QHBoxLayout()
        header.addWidget(avatar)
        header.addSpacing(s.sm)
        header.addWidget(heading)
        header.addStretch()
        layout.addLayout(header)
        for widget in (self.action, self.target, self.impact, self.queued):
            layout.addWidget(widget)
        layout.addLayout(buttons)

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._tick)

    # ------------------------------------------------------------------ fila
    def enqueue(self, request: ConfirmationRequested) -> None:
        self._queue.append(request)
        if self.current is None:
            self._show_next()
        else:
            self._update_queued()

    def discard(self, request_id: UUID) -> None:
        """Pedido encerrado fora do painel (prazo, emergência, outra interface)."""
        self._queue = deque(r for r in self._queue if r.request_id != request_id)
        if self.current is not None and self.current.request_id == request_id:
            self.current = None
            self._show_next()
        else:
            self._update_queued()

    def _show_next(self) -> None:
        if not self._queue:
            self.current = None
            self._ticker.stop()
            self.hide()
            return
        request = self._queue.popleft()
        self.current = request
        self._deadline = time.monotonic() + request.timeout_seconds
        self.action.setText(request.action)
        self.target.setText(request.target)
        self.impact.setText(request.impact)
        self._update_queued()
        self._tick()
        self._ticker.start()
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()
        self.deny.setFocus()

    def _update_queued(self) -> None:
        self.queued.setVisible(bool(self._queue))
        self.queued.setText(f"+{len(self._queue)} pedido(s) aguardando")

    def _tick(self) -> None:
        remaining = max(0, int(self._deadline - time.monotonic() + 0.999))
        self.countdown.setText(f"Recusa automática em {remaining} s")

    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.right() - self.width() - EDGE_MARGIN,
            area.bottom() - self.height() - EDGE_MARGIN - 72,
        )

    # ------------------------------------------------------------------ resposta
    def _answer(self, decision: ConfirmationDecision) -> None:
        if self.current is None:
            return
        self._service.resolve(self.current.request_id, decision)
        self.current = None
        self._show_next()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._answer(ConfirmationDecision.DENIED)
        else:
            super().keyPressEvent(event)


class ConfirmationUiModule(Module):
    name = "confirmation_ui"
    depends_on = ("event_bus", "confirmations")
    optional = True

    def __init__(self, *, bus: EventBus, service: ConfirmationService) -> None:
        self._bus = bus
        self._service = service
        self.panel: ConfirmationPanel | None = None
        self._subscriptions: list[Subscription[Any]] = []

    async def start(self) -> None:
        self.panel = ConfirmationPanel(self._service)
        self._subscriptions = [
            self._bus.subscribe(ConfirmationRequested, self._on_requested, name="confirm-ui"),
            self._bus.subscribe(ConfirmationResolved, self._on_resolved, name="confirm-ui-done"),
        ]

    async def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.unsubscribe()
        if self.panel is not None:
            self.panel.hide()
            self.panel.deleteLater()
            self.panel = None

    async def _on_requested(self, event: ConfirmationRequested) -> None:
        if self.panel is not None:
            self.panel.enqueue(event)

    async def _on_resolved(self, event: ConfirmationResolved) -> None:
        if self.panel is not None:
            self.panel.discard(event.request_id)
