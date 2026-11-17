"""Janela de instalação da IA local (#49), aberta pelo instalador com ``alfred --setup-ai``.

Mostra o mascote, cada passo (Ollama, assinatura, servidor, modelo) e uma barra de progresso real.
Se algo falhar, explica e oferece tentar de novo; o ALFRED continua funcionando sem a IA local.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from alfred.ai.setup import AISetupError, LocalAISetup, SetupResult
from alfred.ui.assets import avatar_pixmap
from alfred.ui.icon import alfred_icon
from alfred.ui.theme import THEME


class AISetupWindow(QWidget):
    def __init__(self, setup: LocalAISetup, on_done: Callable[[int], None]) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("ALFRED — Preparando a inteligência local")
        self.setWindowIcon(alfred_icon())
        self.setFixedWidth(520)
        self._setup = setup
        self._on_done = on_done
        self.result: SetupResult | None = None
        s = THEME.spacing
        layout = QVBoxLayout(self)
        layout.setContentsMargins(s.xxl, s.xxl, s.xxl, s.xl)
        layout.setSpacing(s.md)
        header = QHBoxLayout()
        avatar = QLabel()
        avatar.setPixmap(avatar_pixmap(64))
        titles = QVBoxLayout()
        title = QLabel("Preparando a inteligência local")
        title.setProperty("role", "title")
        subtitle = QLabel("Tudo roda neste computador. Pode levar alguns minutos.")
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addWidget(avatar)
        header.addSpacing(s.md)
        header.addLayout(titles, 1)
        layout.addLayout(header)
        self.status = QLabel("Começando…")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        layout.addSpacing(s.sm)
        layout.addWidget(self.status)
        layout.addWidget(self.bar)
        buttons = QHBoxLayout()
        self.retry = QPushButton("Tentar de novo")
        self.retry.hide()
        self.retry.clicked.connect(self.start)
        self.close_button = QPushButton("Pular")
        self.close_button.setProperty("role", "ghost")
        self.close_button.clicked.connect(lambda: self._finish(1))
        buttons.addStretch()
        buttons.addWidget(self.retry)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self._task: asyncio.Task[None] | None = None

    def progress(self, message: str, fraction: float | None) -> None:
        self.status.setText(message)
        if fraction is None:
            self.bar.setRange(0, 0)  # indeterminado (animação nativa)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(fraction * 1000))

    def start(self) -> None:
        self.retry.hide()
        self.close_button.setText("Pular")
        self._task = asyncio.ensure_future(self.run())

    async def run(self) -> None:
        try:
            self.result = await self._setup.run(self.progress)
        except (AISetupError, OSError, RuntimeError) as exc:
            self.status.setText(f"Não foi possível concluir: {exc}")
            self.bar.setRange(0, 1000)
            self.bar.setValue(0)
            self.retry.show()
            self.close_button.setText("Fechar")
            return
        self.status.setText(
            f"Pronto! A IA local ({self.result.model}) está instalada. "
            "Você pode trocar de modelo ou de provedor nas configurações."
        )
        self.close_button.setText("Concluir")
        self.close_button.setProperty("role", "primary")
        self.close_button.style().unpolish(self.close_button)
        self.close_button.style().polish(self.close_button)

    def _finish(self, code: int) -> None:
        self._on_done(0 if self.result is not None else code)
        self.close()


def run_setup_window(setup: LocalAISetup | None = None) -> int:
    from alfred.ui.qt_runtime import run_on_qt, shutdown_qt

    exit_code = 1
    done = asyncio.Event()

    async def main() -> None:
        def finished(code: int) -> None:
            nonlocal exit_code
            exit_code = code
            done.set()

        window = AISetupWindow(setup or LocalAISetup(), finished)
        window.show()
        window.start()
        await done.wait()

    try:
        run_on_qt(main())
    finally:
        shutdown_qt()
    return exit_code
