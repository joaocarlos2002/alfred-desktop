"""Executa uma corrotina sobre o event loop do Qt (qasync)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

SIGNAL_POLL_MS = 250


def ensure_qt_application() -> QApplication:
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ALFRED")
    app.setOrganizationName("ALFRED")
    # O ALFRED vive na bandeja: fechar janelas não encerra o processo.
    app.setQuitOnLastWindowClosed(False)
    return app


def run_on_qt[T](main: Coroutine[Any, Any, T]) -> T:
    """Roda ``main`` até o fim usando um ``QEventLoop`` como loop asyncio."""
    app = ensure_qt_application()
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    # O loop do Qt roda em C++; um timer periódico devolve o controle ao interpretador para que
    # handlers de sinal (Ctrl+C) sejam executados.
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(SIGNAL_POLL_MS)
    try:
        with loop:
            result: T = loop.run_until_complete(main)
            return result
    finally:
        heartbeat.stop()
        asyncio.set_event_loop(None)
