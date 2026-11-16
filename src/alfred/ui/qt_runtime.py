"""Executa uma corrotina sobre o event loop do Qt (qasync)."""

from __future__ import annotations

import asyncio
import gc
import sys
from collections.abc import Coroutine
from typing import Any

import qasync
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from alfred.ui.theme import apply_theme

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
    apply_theme(app)
    return app


def shutdown_qt() -> None:
    """Destrói widgets e o QApplication de forma ordenada, antes da finalização do Python.

    Sem isso, o coletor do interpretador destrói objetos Qt em ordem arbitrária na saída do
    processo, o que causa falhas de segmentação intermitentes.
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    app.shutdown()


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
