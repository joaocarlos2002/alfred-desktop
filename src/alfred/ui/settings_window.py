"""Painel de configurações gerado a partir dos modelos Pydantic (RF-013).

Seções de funcionalidades que ainda não existem aparecem desativadas, com a fase em que chegam —
nunca com formulários fictícios.
"""

from __future__ import annotations

import asyncio
import json
import logging
import types
from collections.abc import Callable
from typing import Any, Literal, Union, get_args, get_origin

from annotated_types import Ge, Le
from pydantic import ValidationError
from pydantic.fields import FieldInfo
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from alfred.config.events import SettingsChanged
from alfred.config.settings import Settings
from alfred.core.event_bus import EventBus, Subscription
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Module
from alfred.ui.assets import avatar_pixmap
from alfred.ui.icon import alfred_icon
from alfred.ui.theme import THEME

logger = logging.getLogger(__name__)

# Ordem e nomes das seções da RF-013. ``None`` = seção ainda não implementada (fase indicada).
SECTIONS: list[tuple[str, str, str | None]] = [
    ("general", "Geral", None),
    ("appearance", "Aparência", None),
    ("voice", "Voz", None),
    ("activation", "Ativação", None),
    ("ai", "Inteligência artificial", "Fase 5 — Inteligência artificial"),
    ("applications", "Aplicações", "Fase 4 — Automação"),
    ("cursor", "Cursor virtual", None),
    ("triggers", "Gatilhos", "Fase 6 — Autonomia"),
    ("memory", "Memória", "Fase 5 — Inteligência artificial"),
    ("privacy", "Privacidade", None),
    ("permissions", "Permissões", "Fase 5 — Inteligência artificial"),
    ("history", "Histórico", None),
    ("logging", "Registros (logs)", None),
]

FIELD_LABELS = {
    "language": "Idioma",
    "start_with_windows": "Iniciar com o Windows",
    "theme": "Tema",
    "widget_visible": "Mostrar widget flutuante",
    "animations": "Animações",
    "activate_hotkey": "Atalho de ativação",
    "emergency_hotkey": "Atalho de emergência",
    "level": "Nível de log",
    "module_levels": "Níveis por módulo (JSON)",
    "max_bytes": "Tamanho máximo do arquivo (bytes)",
    "backup_count": "Arquivos de log mantidos",
    "network_enabled": "Permitir acesso à rede",
    "screen_capture": "Captura de tela pela IA",
    "enabled": "Ativado",
    "input_device": "Microfone",
    "wake_word_enabled": 'Ativação pela palavra "Alfred"',
    "wake_word_confidence": "Confiança mínima da palavra",
    "stt_model": "Modelo de reconhecimento",
    "stt_idle_unload_seconds": "Liberar modelo após (s)",
    "output_device": "Saída de áudio",
    "speed": "Velocidade (px/s)",
    "size": "Tamanho (px)",
}
CHOICE_LABELS = {
    "ask": "Perguntar",
    "allow": "Permitir",
    "deny": "Negar",
    "dark": "Escuro",
    "pt-BR": "Português (Brasil)",
    "tiny": "Rápido (tiny)",
    "base": "Equilibrado (base)",
    "small": "Preciso (small)",
}
HIDDEN_FIELDS = {"widget_position"}
"""Campos ajustados por interação direta (ex.: arrastar o widget), não por formulário."""

type Getter = Callable[[], Any]
type ChoiceProviders = dict[str, Callable[[], list[str]]]
"""Opções dinâmicas por nome de campo (ex.: dispositivos de áudio detectados)."""

SYSTEM_DEFAULT = "Padrão do sistema"


def _is_section(name: str) -> bool:
    return name in Settings.section_names()


def _bounds(info: FieldInfo) -> tuple[float, float]:
    low: float = 0
    high: float = 2_000_000_000
    for meta in info.metadata:
        if isinstance(meta, Ge):
            low = float(meta.ge)  # type: ignore[arg-type]
        elif isinstance(meta, Le):
            high = float(meta.le)  # type: ignore[arg-type]
    return low, high


def build_editor(
    info: FieldInfo, value: Any, choices: list[str] | None = None
) -> tuple[QWidget, Getter]:
    """Cria o editor adequado ao tipo do campo e uma função que lê o valor editado."""
    annotation = info.annotation
    optional = False
    if get_origin(annotation) in (Union, types.UnionType):
        optional = type(None) in get_args(annotation)
        annotation = next(a for a in get_args(annotation) if a is not type(None))
    if choices is not None:
        combo = QComboBox()
        if optional:
            combo.addItem(SYSTEM_DEFAULT, None)
        for choice in choices:
            combo.addItem(choice, choice)
        if value is not None and combo.findData(value) < 0:
            combo.addItem(f"{value} (desconectado)", value)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        return combo, combo.currentData
    if annotation is bool:
        checkbox = QCheckBox()
        checkbox.setChecked(bool(value))
        return checkbox, checkbox.isChecked
    if get_origin(annotation) is Literal:
        combo = QComboBox()
        for choice in get_args(annotation):
            combo.addItem(CHOICE_LABELS.get(choice, str(choice)), choice)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        return combo, combo.currentData
    if annotation is float:
        double = QDoubleSpinBox()
        low, high = _bounds(info)
        double.setRange(float(low), float(high) if high < 2_000_000_000 else 1e9)
        double.setDecimals(2)
        double.setSingleStep(0.01)
        double.setValue(float(value))
        return double, lambda: round(double.value(), 2)
    if annotation is int:
        spin = QSpinBox()
        low, high = _bounds(info)
        spin.setRange(int(low), int(high))
        spin.setValue(int(value))
        return spin, spin.value
    if get_origin(annotation) is dict:
        text = QPlainTextEdit(json.dumps(value, ensure_ascii=False, indent=2))
        text.setFixedHeight(90)

        def read_json() -> Any:
            raw = text.toPlainText().strip() or "{}"
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON inválido: {exc.msg}") from exc

        return text, read_json
    line = QLineEdit("" if value is None else str(value))
    if optional:
        return line, lambda: line.text().strip() or None
    return line, line.text


class SectionForm(QWidget):
    """Formulário de uma seção real das configurações."""

    def __init__(
        self, section: str, settings: Settings, choices: ChoiceProviders | None = None
    ) -> None:
        super().__init__()
        self.section = section
        self._choices = choices or {}
        self._getters: dict[str, Getter] = {}
        self._form = QFormLayout(self)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self._form.setVerticalSpacing(THEME.spacing.md)
        self.load(settings)

    def load(self, settings: Settings) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
        self._getters.clear()
        current = settings.section(self.section)
        for name, info in type(current).model_fields.items():
            if name in HIDDEN_FIELDS:
                continue
            provider = self._choices.get(name)
            options = _safe_choices(provider) if provider else None
            editor, getter = build_editor(info, getattr(current, name), options)
            editor.setObjectName(name)
            if info.description:
                editor.setToolTip(info.description)
            self._getters[name] = getter
            label = QLabel(FIELD_LABELS.get(name, name))
            if info.description:
                label.setToolTip(info.description)
            self._form.addRow(label, editor)

    def changes(self, settings: Settings) -> dict[str, Any]:
        """Campos cujo valor no formulário difere do atual. Raises: ValueError (ex.: JSON)."""
        current = settings.section(self.section)
        changed: dict[str, Any] = {}
        for name, getter in self._getters.items():
            value = getter()
            if value != getattr(current, name):
                changed[name] = value
        return changed

    def editor(self, name: str) -> QWidget:
        widget = self.findChild(QWidget, name)
        assert widget is not None, name
        return widget


def _safe_choices(provider: Callable[[], list[str]]) -> list[str]:
    try:
        return provider()
    except Exception:
        logger.exception("Falha ao listar opções")
        return []


class UnavailableSection(QWidget):
    def __init__(self, title: str, phase: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        message = QLabel(
            f"As configurações de {title.lower()} chegam junto com a funcionalidade, na {phase}."
        )
        message.setWordWrap(True)
        message.setProperty("role", "muted")
        layout.addWidget(message)
        layout.addStretch()


class HistorySection(QWidget):
    def __init__(self, on_open_panel: Callable[[], None] | None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        message = QLabel(
            "Tarefas executadas, confirmações e interrupções ficam no painel de atividades, "
            "armazenadas apenas neste computador."
        )
        message.setWordWrap(True)
        message.setProperty("role", "muted")
        layout.addWidget(message)
        if on_open_panel is not None:
            button = QPushButton("Abrir painel de atividades")
            button.clicked.connect(on_open_panel)
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()


class SettingsWindow(QWidget):
    def __init__(
        self,
        settings: SettingsService,
        *,
        on_open_panel: Callable[[], None] | None = None,
        choices: ChoiceProviders | None = None,
    ) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("ALFRED — Configurações")
        self.setWindowIcon(alfred_icon())
        self.resize(780, 600)
        self._settings = settings
        self.forms: dict[str, SectionForm] = {}
        self._pending: set[asyncio.Task[bool]] = set()
        s = THEME.spacing

        root = QHBoxLayout(self)
        root.setContentsMargins(s.lg, s.lg, s.lg, s.lg)
        self.sections = QListWidget()
        self.sections.setObjectName("sections")
        self.sections.setFixedWidth(250)
        self.sections.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages = QStackedWidget()
        for key, title, phase in SECTIONS:
            item = QListWidgetItem(title if phase is None else f"{title}  ·  em breve")
            item.setData(Qt.ItemDataRole.UserRole, key)
            if phase is not None:
                item.setForeground(THEME.color("text_muted"))
                item.setToolTip(f"Disponível na {phase}")
                page: QWidget = UnavailableSection(title, phase)
            elif key == "history":
                page = HistorySection(on_open_panel)
            else:
                form = SectionForm(key, settings.current, choices)
                self.forms[key] = form
                page = form
            self.sections.addItem(item)
            self.pages.addWidget(page)
        self.sections.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sections.setCurrentRow(0)

        right = QVBoxLayout()
        header = QHBoxLayout()
        avatar = QLabel()
        avatar.setPixmap(avatar_pixmap(40))
        title_label = QLabel("Configurações")
        title_label.setProperty("role", "title")
        header.addWidget(avatar)
        header.addSpacing(s.sm)
        header.addWidget(title_label)
        header.addStretch()
        right.addLayout(header)
        right.addWidget(self.pages, 1)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.revert_button = QPushButton("Desfazer alterações")
        self.apply_button = QPushButton("Aplicar")
        self.apply_button.setProperty("role", "primary")
        self.revert_button.clicked.connect(self.reload)
        self.apply_button.clicked.connect(self._apply_clicked)
        buttons = QHBoxLayout()
        buttons.addWidget(self.feedback, 1)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.apply_button)
        right.addLayout(buttons)

        root.addWidget(self.sections)
        root.addLayout(right, 1)

    def select(self, key: str) -> None:
        for row in range(self.sections.count()):
            if self.sections.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.sections.setCurrentRow(row)

    def reload(self) -> None:
        for form in self.forms.values():
            form.load(self._settings.current)
        self._set_feedback("", error=False)

    def _apply_clicked(self) -> None:
        task = asyncio.ensure_future(self.apply())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def apply(self) -> bool:
        """Valida e salva todas as seções alteradas. Nada é salvo de uma seção inválida."""
        errors: list[str] = []
        saved = 0
        for key, form in self.forms.items():
            try:
                changes = form.changes(self._settings.current)
                if changes:
                    await self._settings.update(key, **changes)
                    saved += 1
            except ValidationError as exc:
                errors.extend(_describe(key, exc))
            except (ValueError, KeyError) as exc:
                errors.append(f"{_section_title(key)}: {exc}")
        if errors:
            self._set_feedback("Não salvo — " + "; ".join(errors), error=True)
            return False
        self._set_feedback(
            "Configurações salvas." if saved else "Nenhuma alteração para salvar.", error=False
        )
        return True

    def _set_feedback(self, text: str, *, error: bool) -> None:
        self.feedback.setText(text)
        self.feedback.setProperty("role", "error" if error else "muted")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)


def _section_title(key: str) -> str:
    return next(title for k, title, _ in SECTIONS if k == key)


def _describe(section: str, exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else section
        message = str(error["msg"]).removeprefix("Value error, ")
        messages.append(f"{FIELD_LABELS.get(field, field)}: {message}")
    return messages


class SettingsWindowModule(Module):
    name = "settings_window"
    depends_on = ("event_bus", "settings")
    optional = True

    def __init__(
        self,
        *,
        bus: EventBus,
        settings: SettingsService,
        on_open_panel: Callable[[], None] | None = None,
        choices: ChoiceProviders | None = None,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._on_open_panel = on_open_panel
        self._choices = choices
        self.window: SettingsWindow | None = None
        self._subscription: Subscription[SettingsChanged] | None = None

    async def start(self) -> None:
        self._subscription = self._bus.subscribe(
            SettingsChanged, self._on_changed, name="settings-window"
        )

    async def stop(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
            self.window = None

    def show(self) -> None:
        if self.window is None:
            self.window = SettingsWindow(
                self._settings, on_open_panel=self._on_open_panel, choices=self._choices
            )
        else:
            self.window.reload()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    async def _on_changed(self, event: SettingsChanged) -> None:
        # Alteração vinda de fora (bandeja, arrastar o widget): atualiza a seção exibida.
        if self.window is not None and _is_section(event.section):
            form = self.window.forms.get(event.section)
            if form is not None and not form.changes(event.settings):
                return
            if form is not None:
                form.load(event.settings)
