"""Aplicação de teste para os E2E de UI Automation (não contém nem lê dados do usuário).

Uso: ``python uia_app.py "<título da janela>" "<texto de origem>"``

- campo somente leitura **Origem** com o texto informado;
- campo **Entrada** editável e botão **Confirmar**;
- campo somente leitura **Resultado**, que passa a mostrar ``recebido: <entrada>`` ao confirmar.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget


def field(name: str, text: str = "", *, read_only: bool = False) -> QLineEdit:
    edit = QLineEdit(text)
    edit.setObjectName(name)
    edit.setAccessibleName(name)
    edit.setReadOnly(read_only)
    return edit


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "ALFRED Teste UIA"
    origin = sys.argv[2] if len(sys.argv) > 2 else ""
    app = QApplication(sys.argv[:1])
    window = QWidget()
    window.setWindowTitle(title)
    layout = QVBoxLayout(window)
    source = field("Origem", origin, read_only=True)
    entry = field("Entrada")
    result = field("Resultado", "vazio", read_only=True)
    confirm = QPushButton("Confirmar")
    confirm.setObjectName("Confirmar")
    confirm.setAccessibleName("Confirmar")
    confirm.clicked.connect(lambda: result.setText(f"recebido: {entry.text()}"))
    for widget in (source, entry, confirm, result):
        layout.addWidget(widget)
    window.resize(360, 200)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
