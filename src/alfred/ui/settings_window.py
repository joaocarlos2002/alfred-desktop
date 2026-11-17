"""Painel de configurações gerado a partir dos modelos Pydantic (RF-013).

Seções de funcionalidades que ainda não existem aparecem desativadas, com a fase em que chegam —
nunca com formulários fictícios.
"""

from __future__ import annotations

import asyncio
import json
import logging
import types
from collections.abc import Callable, Mapping
from typing import Any, ClassVar, Literal, Union, get_args, get_origin

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
    QMessageBox,
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
from alfred.core.privacy import data_flows
from alfred.core.settings_service import SettingsService
from alfred.domain.module import Module
from alfred.domain.triggers import KIND_LABELS, SYSTEM_EVENTS, TriggerCondition, TriggerSpec
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
    ("ai", "Inteligência artificial", None),
    ("applications", "Aplicações", "Fase 4 — Automação"),
    ("cursor", "Cursor virtual", None),
    ("triggers", "Gatilhos", None),
    ("skills", "Skills", None),
    ("memory", "Memória", None),
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
    "provider": "Provedor",
    "model": "Modelo",
    "timeout_seconds": "Tempo máximo de resposta (s)",
    "max_retries": "Novas tentativas",
    "external_base_url": "URL do provedor externo",
    "input_device": "Microfone",
    "wake_word_enabled": 'Ativação pela palavra "Alfred"',
    "wake_word_confidence": "Confiança mínima da palavra",
    "stt_model": "Modelo de reconhecimento",
    "tts_engine": "Voz das respostas",
    "tts_voice": "Voz do Windows",
    "tts_rate": "Velocidade da fala",
    "tts_volume": "Volume da fala",
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
    "sapi": "Windows (SAPI)",
    "piper": "Neural local (Piper)",
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


class MemorySection(QWidget):
    """Consultar, adicionar, editar, excluir e apagar tudo o que o ALFRED lembra (RF-012)."""

    def __init__(self, memory: Callable[[], Any] | None) -> None:
        super().__init__()
        self._memory = memory
        self._pending: set[asyncio.Task[None]] = set()
        layout = QVBoxLayout(self)
        intro = QLabel(
            "O que o ALFRED lembra sobre você. Fica só neste computador e é usado como contexto "
            "da inteligência artificial. Dê dois cliques para editar."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        self.list = QListWidget()
        self.list.itemChanged.connect(self._edited)
        self.new_item = QLineEdit()
        self.new_item.setPlaceholderText("Ex.: Prefiro respostas curtas.")
        add = QPushButton("Lembrar")
        add.clicked.connect(lambda: self._run(self._add()))
        delete = QPushButton("Excluir selecionada")
        delete.clicked.connect(lambda: self._run(self._delete()))
        forget = QPushButton("Esquecer tudo")
        forget.setProperty("role", "danger")
        forget.clicked.connect(self._confirm_forget)
        self.new_chat = QPushButton("Nova conversa")
        self.new_chat.setToolTip(
            "O ALFRED lembra das últimas trocas da conversa. Comece outra (ou diga "
            '"Alfred, nova conversa").'
        )
        self.new_chat.clicked.connect(lambda: self._run(self._new_conversation()))
        self.feedback = QLabel()
        self.feedback.setProperty("role", "muted")
        row = QHBoxLayout()
        row.addWidget(self.new_item, 1)
        row.addWidget(add)
        actions = QHBoxLayout()
        actions.addWidget(delete)
        actions.addWidget(self.new_chat)
        actions.addStretch()
        actions.addWidget(forget)
        for widget in (intro, self.list):
            layout.addWidget(widget)
        layout.addLayout(row)
        layout.addLayout(actions)
        layout.addWidget(self.feedback)
        self._run(self.reload())

    def _service(self) -> Any:
        return self._memory() if self._memory is not None else None

    def _run(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # sem loop asyncio (ex.: pré-visualização)
            coro.close()
            return
        task = loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def reload(self) -> None:
        service = self._service()
        if service is None:
            self.feedback.setText("Memória indisponível.")
            return
        self.list.blockSignals(True)
        self.list.clear()
        for item in await service.memories():
            entry = QListWidgetItem(item.content)
            entry.setData(Qt.ItemDataRole.UserRole, item.id)
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsEditable)
            self.list.addItem(entry)
        self.list.blockSignals(False)
        self.feedback.setText(f"{self.list.count()} memória(s).")

    async def _add(self) -> None:
        text = self.new_item.text().strip()
        service = self._service()
        if text and service is not None:
            await service.remember(text)
            self.new_item.clear()
            await self.reload()

    async def _delete(self) -> None:
        item = self.list.currentItem()
        service = self._service()
        if item is not None and service is not None:
            await service.forget(int(item.data(Qt.ItemDataRole.UserRole)))
            await self.reload()

    def _edited(self, item: QListWidgetItem) -> None:
        service = self._service()
        if service is not None:
            self._run(service.edit(int(item.data(Qt.ItemDataRole.UserRole)), item.text()))

    def _confirm_forget(self) -> None:
        answer = QMessageBox.question(
            self,
            "Esquecer tudo",
            "Apagar todas as memórias e conversas do ALFRED? Isso não pode ser desfeito.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run(self._forget_all())

    async def _new_conversation(self) -> None:
        service = self._service()
        if service is not None:
            await service.new_conversation()
            self.feedback.setText("Nova conversa iniciada: as trocas anteriores não serão usadas.")

    async def _forget_all(self) -> None:
        service = self._service()
        if service is not None:
            await service.forget_everything()
            await self.reload()


class TriggersSection(QWidget):
    """Criar, editar, habilitar, desabilitar e excluir gatilhos (RF-011)."""

    PLACEHOLDERS: ClassVar[dict[str, str]] = {
        "voice": "Frase, ex.: modo trabalho",
        "hotkey": "Atalho, ex.: ctrl+alt+t",
        "schedule": "Cron, ex.: 0 8 * * 1-5 (8h, seg a sex)",
        "startup": "(sem configuração)",
        "app_open": "Executável, ex.: winword.exe",
        "system": "Evento: " + ", ".join(SYSTEM_EVENTS),
    }
    SOURCE_KEYS: ClassVar[dict[str, str]] = {
        "voice": "frase",
        "hotkey": "atalho",
        "schedule": "cron",
        "app_open": "app",
        "system": "evento",
    }
    DAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

    def __init__(self, triggers: Callable[[], Any] | None) -> None:
        super().__init__()
        self._triggers = triggers
        self._pending: set[asyncio.Task[None]] = set()
        self._specs: dict[int, TriggerSpec] = {}
        self._editing: int | None = None
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Gatilhos entregam um pedido ao ALFRED automaticamente. A execução passa pelas mesmas "
            "permissões e confirmações de um pedido falado. Marque/desmarque para habilitar."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        self.list = QListWidget()
        self.list.itemChanged.connect(self._toggled)
        self.list.currentItemChanged.connect(lambda item, _: self._load(item))
        form = QFormLayout()
        self.name = QLineEdit()
        self.kind = QComboBox()
        for key, label in KIND_LABELS.items():
            self.kind.addItem(label, key)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.source = QLineEdit()
        self.request = QLineEdit()
        self.request.setPlaceholderText("Ex.: abra o Outlook e o Teams")
        self.hours = QLineEdit()
        self.hours.setPlaceholderText("Opcional, ex.: 08:00-18:00")
        days = QHBoxLayout()
        self.days: list[QCheckBox] = []
        for label in self.DAYS:
            box = QCheckBox(label)
            self.days.append(box)
            days.addWidget(box)
        form.addRow("Nome", self.name)
        form.addRow("Fonte", self.kind)
        form.addRow("Configuração", self.source)
        form.addRow("Pedido", self.request)
        form.addRow("Horário", self.hours)
        form.addRow("Dias (vazio = todos)", days)
        buttons = QHBoxLayout()
        new = QPushButton("Novo")
        new.clicked.connect(self._new)
        save = QPushButton("Salvar")
        save.setProperty("role", "primary")
        save.clicked.connect(lambda: self._run(self._save()))
        delete = QPushButton("Excluir")
        delete.setProperty("role", "danger")
        delete.clicked.connect(lambda: self._run(self._delete()))
        buttons.addWidget(new)
        buttons.addStretch()
        buttons.addWidget(delete)
        buttons.addWidget(save)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setProperty("role", "muted")
        layout.addWidget(intro)
        layout.addWidget(self.list, 1)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.feedback)
        self._kind_changed()
        self._run(self.reload())

    def _engine(self) -> Any:
        return self._triggers() if self._triggers is not None else None

    def _run(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # sem loop asyncio (ex.: pré-visualização)
            coro.close()
            return
        task = loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        self.source.setPlaceholderText(self.PLACEHOLDERS.get(kind, ""))
        self.source.setEnabled(kind != "startup")

    async def reload(self) -> None:
        engine = self._engine()
        if engine is None:
            self.feedback.setText("Motor de gatilhos indisponível.")
            return
        specs = await engine.triggers()
        self._specs = {spec.id: spec for spec in specs}
        problems = getattr(engine, "problems", {})
        self.list.blockSignals(True)
        self.list.clear()
        for spec in specs:
            text = f"{spec.name} — {KIND_LABELS[spec.kind]}"
            if spec.source_value():
                text += f": {spec.source_value()}"
            if spec.id in problems:
                text += f"  (inativo: {problems[spec.id]})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, spec.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if spec.enabled else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.feedback.setText(f"{len(specs)} gatilho(s).")

    def _load(self, item: QListWidgetItem | None) -> None:
        spec = self._specs.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if spec is None:
            return
        self._editing = spec.id
        self.name.setText(spec.name)
        self.kind.setCurrentIndex(self.kind.findData(spec.kind))
        self.source.setText(spec.source_value())
        self.request.setText(spec.request)
        self.hours.setText(spec.condition.hours or "")
        for index, box in enumerate(self.days):
            box.setChecked(index in spec.condition.weekdays)

    def _new(self) -> None:
        self._editing = None
        self.list.clearSelection()
        for field in (self.name, self.source, self.request, self.hours):
            field.clear()
        for box in self.days:
            box.setChecked(False)

    def _spec(self) -> TriggerSpec:
        kind = self.kind.currentData()
        config: dict[str, Any] = {}
        if kind in self.SOURCE_KEYS:
            config[self.SOURCE_KEYS[kind]] = self.source.text().strip()
        previous = self._specs.get(self._editing) if self._editing is not None else None
        if previous is not None and previous.kind == kind:
            config = {**previous.config, **config}
        return TriggerSpec(
            id=self._editing,
            name=self.name.text().strip(),
            kind=kind,
            config=config,
            condition=TriggerCondition(
                hours=self.hours.text().strip() or None,
                weekdays=tuple(i for i, box in enumerate(self.days) if box.isChecked()),
            ),
            request=self.request.text().strip(),
            enabled=previous.enabled if previous is not None else True,
        )

    async def _save(self) -> None:
        engine = self._engine()
        if engine is None:
            return
        try:
            saved = await engine.save(self._spec())
        except (ValidationError, ValueError) as exc:
            self.feedback.setText(f"Não foi possível salvar: {_first_error(exc)}")
            return
        except Exception as exc:  # ex.: nome repetido
            self.feedback.setText(f"Não foi possível salvar: {exc}")
            return
        self._editing = saved.id
        await self.reload()
        self.feedback.setText(f"Gatilho '{saved.name}' salvo.")

    async def _delete(self) -> None:
        engine = self._engine()
        if engine is None or self._editing is None:
            return
        await engine.delete(self._editing)
        self._new()
        await self.reload()

    def _toggled(self, item: QListWidgetItem) -> None:
        engine = self._engine()
        if engine is None:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        self._run(self._set_enabled(engine, int(item.data(Qt.ItemDataRole.UserRole)), enabled))

    async def _set_enabled(self, engine: Any, trigger_id: int, enabled: bool) -> None:
        await engine.set_enabled(trigger_id, enabled)
        await self.reload()


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        return str(error.get("msg", exc))
    return str(exc)


class SkillsSection(QWidget):
    """Liga e desliga skills (#123); mostra se cada uma está ativa ou com problema."""

    def __init__(self, skills: Callable[[], Any] | None) -> None:
        super().__init__()
        self._skills = skills
        self._pending: set[asyncio.Task[None]] = set()
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Skills são capacidades extras do ALFRED. Marque para ativar; as ferramentas de cada "
            "skill continuam passando pelas mesmas permissões e confirmações."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        self.list = QListWidget()
        self.list.itemChanged.connect(self._toggled)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setProperty("role", "muted")
        for widget in (intro, self.list, self.feedback):
            layout.addWidget(widget)
        self.reload()

    def _manager(self) -> Any:
        return self._skills() if self._skills is not None else None

    def reload(self) -> None:
        manager = self._manager()
        self.list.blockSignals(True)
        self.list.clear()
        statuses = manager.statuses() if manager is not None else []
        for status in statuses:
            state = (
                "ativa" if status.active else ("com problema" if status.problem else "desligada")
            )
            item = QListWidgetItem(f"{status.name} — {status.description}  ({state})")
            item.setData(Qt.ItemDataRole.UserRole, status.key)
            item.setToolTip(status.problem or status.description)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if status.enabled else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.feedback.setText(
            "Nenhuma skill instalada."
            if not statuses
            else f"{len(statuses)} skill(s) disponível(is)."
        )

    def _toggled(self, item: QListWidgetItem) -> None:
        manager = self._manager()
        if manager is None:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        key = str(item.data(Qt.ItemDataRole.UserRole))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._apply(manager, key, enabled))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _apply(self, manager: Any, key: str, enabled: bool) -> None:
        await manager.set_enabled(key, enabled)
        self.reload()


class PrivacyOverview(QWidget):
    """Estado de cada fluxo de dados (Seção 9): o que fica no computador e o que pode sair."""

    def __init__(self, providers: Callable[[], Mapping[str, bool]] | None) -> None:
        super().__init__()
        self._providers = providers
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Fluxos de dados")
        title.setProperty("role", "subtitle")
        self.list = QListWidget()
        self.list.setMaximumHeight(230)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "muted")
        for widget in (title, self.summary, self.list):
            layout.addWidget(widget)

    def refresh(self, settings: Settings) -> None:
        providers = self._providers() if self._providers is not None else {}
        flows = data_flows(settings, providers)
        self.list.clear()
        for flow in flows:
            mark = "⇄ sai do computador" if flow.leaves_computer else "● local"
            item = QListWidgetItem(f"{flow.label}: {flow.state}   ({mark})")
            item.setToolTip(flow.detail)
            self.list.addItem(item)
        leaving = [f.label for f in flows if f.leaves_computer]
        self.summary.setText(
            "Nada sai deste computador agora."
            if not leaving
            else "Saindo do computador agora: " + ", ".join(leaving) + "."
        )


class PrivacyPage(QWidget):
    def __init__(self, form: SectionForm, overview: PrivacyOverview) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(overview)
        layout.addWidget(form, 1)


class SettingsWindow(QWidget):
    def __init__(
        self,
        settings: SettingsService,
        *,
        on_open_panel: Callable[[], None] | None = None,
        choices: ChoiceProviders | None = None,
        memory: Callable[[], Any] | None = None,
        triggers: Callable[[], Any] | None = None,
        ai_providers: Callable[[], Mapping[str, bool]] | None = None,
        skills: Callable[[], Any] | None = None,
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
            elif key == "memory":
                page = MemorySection(memory)
            elif key == "triggers":
                page = TriggersSection(triggers)
            elif key == "skills":
                page = SkillsSection(skills)
            elif key == "privacy":
                form = SectionForm(key, settings.current, choices)
                self.forms[key] = form
                self.privacy = PrivacyOverview(ai_providers)
                self.privacy.refresh(settings.current)
                page = PrivacyPage(form, self.privacy)
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
        self.privacy.refresh(self._settings.current)
        # Dados que podem ter mudado com a janela fechada (ex.: memória por voz).
        for memory in self.findChildren(MemorySection):
            memory._run(memory.reload())
        for triggers in self.findChildren(TriggersSection):
            triggers._run(triggers.reload())
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
        self.privacy.refresh(self._settings.current)
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
        memory: Callable[[], Any] | None = None,
        triggers: Callable[[], Any] | None = None,
        ai_providers: Callable[[], Mapping[str, bool]] | None = None,
        skills: Callable[[], Any] | None = None,
    ) -> None:
        self._memory = memory
        self._triggers = triggers
        self._ai_providers = ai_providers
        self._skills = skills
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
                self._settings,
                on_open_panel=self._on_open_panel,
                choices=self._choices,
                memory=self._memory,
                triggers=self._triggers,
                ai_providers=self._ai_providers,
                skills=self._skills,
            )
        else:
            self.window.reload()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    async def _on_changed(self, event: SettingsChanged) -> None:
        # Alteração vinda de fora (bandeja, arrastar o widget): atualiza a seção exibida.
        if self.window is not None:
            self.window.privacy.refresh(event.settings)
        if self.window is not None and _is_section(event.section):
            form = self.window.forms.get(event.section)
            if form is not None and not form.changes(event.settings):
                return
            if form is not None:
                form.load(event.settings)
