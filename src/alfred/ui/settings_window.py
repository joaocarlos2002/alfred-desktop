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
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QRadialGradient
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
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
from alfred.ui.components import Card, Divider, EmptyState, SectionHeader, Toggle, fade_in
from alfred.ui.icon import alfred_icon
from alfred.ui.theme import THEME

logger = logging.getLogger(__name__)

# Ordem e nomes das seções da RF-013. ``None`` = seção ainda não implementada (fase indicada).
SECTIONS: list[tuple[str, str, str | None]] = [
    ("general", "Geral", None),
    ("appearance", "Aparência", None),
    ("activation", "Ativação", None),
    ("voice", "Voz", None),
    ("ai", "Inteligência artificial", None),
    ("profile", "Perfil", None),
    ("memory", "Memória", None),
    ("skills", "Skills", None),
    ("applications", "Aplicações", None),
    ("triggers", "Gatilhos", None),
    ("cursor", "Cursor virtual", None),
    ("permissions", "Permissões", None),
    ("privacy", "Privacidade", None),
    ("history", "Histórico", None),
    ("logging", "Registros (logs)", None),
]

SECTION_INFO: dict[str, tuple[str, str]] = {
    "general": ("Essencial", "Idioma e inicialização junto com o Windows."),
    "appearance": ("Essencial", "Widget flutuante, tema e animações da interface."),
    "activation": ("Essencial", "Atalhos globais para chamar e interromper o ALFRED."),
    "voice": ("Voz", "Microfone, palavra de ativação, transcrição e a voz das respostas."),
    "ai": ("Inteligência", "Provedor de IA, modelos e memória da conversa."),
    "profile": ("Inteligência", "Como o ALFRED fala com você: tamanho, tom e tratamento."),
    "memory": ("Inteligência", "O que o ALFRED lembra sobre você — só neste computador."),
    "skills": ("Inteligência", "Capacidades extras que você liga e desliga."),
    "applications": ("Inteligência", "O que o ALFRED aprendeu sobre os seus programas."),
    "triggers": ("Automação", "Ações automáticas por voz, atalho, horário e eventos."),
    "cursor": ("Automação", "O cursor virtual que mostra onde o ALFRED está agindo."),
    "permissions": ("Automação", "O que o ALFRED pode fazer sozinho e o que precisa perguntar."),
    "privacy": ("Privacidade e dados", "Para onde vão os seus dados — e o que nunca sai daqui."),
    "history": ("Privacidade e dados", "Tarefas executadas, com resultados reais."),
    "logging": ("Privacidade e dados", "Registros técnicos para diagnóstico."),
}

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
    "learning_mode": "Aprender com as conversas",
    "response_length": "Tamanho das respostas",
    "tone": "Tom",
    "address": "Como chamar você",
    "use_habits": "Usar meus hábitos",
    "conversation_turns": "Trocas lembradas da conversa",
    "conversation_idle_minutes": "Nova conversa após (min)",
    "conversation_char_budget": "Tamanho do contexto da conversa",
    "vision_provider": "Provedor para análise de tela",
    "vision_model": "Modelo multimodal",
    "max_parallel_steps": "Etapas em paralelo",
    "load_timeout_seconds": "Tempo extra para carregar o modelo (s)",
    "start_local_server": "Iniciar o Ollama automaticamente",
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
    "suggest": "Sugerir para eu aprovar",
    "short": "Curtas",
    "detailed": "Detalhadas",
    "formal": "Formal",
    "casual": "Descontraído",
    "auto": "Automático",
    "off": "Desligado",
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
        toggle = Toggle(bool(value))
        return toggle, toggle.isChecked
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._card = Card(padding=0)
        outer.addWidget(self._card)
        outer.addStretch()
        self.load(settings)

    def load(self, settings: Settings) -> None:
        body = self._card.body
        while body.count():
            item = body.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)  # some já da árvore (findChild não acha o antigo)
                widget.deleteLater()
        self._getters.clear()
        current = settings.section(self.section)
        first = True
        for name, info in type(current).model_fields.items():
            if name in HIDDEN_FIELDS:
                continue
            provider = self._choices.get(name)
            options = _safe_choices(provider) if provider else None
            editor, getter = build_editor(info, getattr(current, name), options)
            editor.setObjectName(name)
            if info.description:
                editor.setToolTip(info.description)
                editor.setAccessibleDescription(info.description)
            self._getters[name] = getter
            if not first:
                body.addWidget(Divider())
            first = False
            body.addWidget(_setting_row(FIELD_LABELS.get(name, name), info.description, editor))

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


def _setting_row(title: str, hint: str | None, editor: QWidget) -> QWidget:
    """Uma configuração: nome e explicação à esquerda, controle à direita."""
    s = THEME.spacing
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(s.lg + s.xs, s.md, s.lg + s.xs, s.md)
    layout.setSpacing(s.xl)
    texts = QVBoxLayout()
    texts.setSpacing(s.xxs)
    name = QLabel(title)
    name.setProperty("role", "setting")
    texts.addWidget(name)
    if hint:
        explanation = QLabel(hint)
        explanation.setProperty("role", "hint")
        explanation.setWordWrap(True)
        texts.addWidget(explanation)
    layout.addLayout(texts, 1)
    if isinstance(editor, QPlainTextEdit | QLineEdit | QComboBox):
        editor.setMinimumWidth(240)
    layout.addWidget(editor, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


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


TOOL_CATEGORY_LABELS = {
    "applications": ("Aplicativos", "Abrir e fechar programas."),
    "windows": ("Janelas", "Focar, minimizar, maximizar e mover janelas."),
    "ui": ("Botões e campos", "Clicar e preencher elementos via UI Automation."),
    "input": ("Teclado e mouse", "Digitar e usar atalhos no programa em foco."),
    "browser": ("Navegador", "Abrir e ler páginas no navegador do ALFRED."),
    "screen": ("Tela", "Capturar e analisar a tela."),
    "skills": ("Skills", "Ferramentas das skills (ex.: aprender programas)."),
}
POLICY_CHOICES = (("Padrão", None), ("Permitir", "allow"), ("Perguntar", "ask"), ("Negar", "deny"))


class PermissionsSection(QWidget):
    """Política por categoria de ação (PermissionService), com as sensíveis sempre confirmando."""

    def __init__(
        self, permissions: Callable[[], Any] | None, tools: Callable[[], Any] | None
    ) -> None:
        super().__init__()
        self._permissions = permissions
        self._tools = tools
        self._pending: set[asyncio.Task[None]] = set()
        self.combos: dict[str, QComboBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._card = Card(padding=0)
        layout.addWidget(self._card)
        note = QLabel(
            "Padrão: leitura e ações comuns são permitidas; ações sensíveis perguntam. Categorias "
            "sensíveis (arquivos, mensagens, compras, sistema) sempre pedem confirmação."
        )
        note.setWordWrap(True)
        note.setProperty("role", "hint")
        layout.addWidget(note)
        self.feedback = QLabel()
        self.feedback.setProperty("role", "muted")
        layout.addWidget(self.feedback)
        layout.addStretch()
        self.reload()

    def reload(self) -> None:
        from alfred.core.permissions import ALWAYS_CONFIRM, CATEGORY_LABELS

        body = self._card.body
        while body.count():
            item = body.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)  # some já da árvore (findChild não acha o antigo)
                widget.deleteLater()
        self.combos.clear()
        service = self._permissions() if self._permissions is not None else None
        registry = self._tools() if self._tools is not None else None
        categories = sorted(set(registry.categories() if registry else []) | ALWAYS_CONFIRM)
        if service is None:
            self.feedback.setText("Serviço de permissões indisponível.")
            return
        for index, category in enumerate(categories):
            sensitive = category in ALWAYS_CONFIRM
            title, hint = TOOL_CATEGORY_LABELS.get(
                category, (CATEGORY_LABELS.get(category, category).capitalize(), "")
            )
            combo = QComboBox()
            for label, value in POLICY_CHOICES:
                if sensitive and value == "allow":
                    continue  # sensível nunca roda sem confirmação
                combo.addItem(label, value)
            current = service.category_policy(category)
            combo.setCurrentIndex(max(0, combo.findData(current.value if current else None)))
            combo.currentIndexChanged.connect(
                lambda _i, c=category, box=combo: self._changed(c, box.currentData())
            )
            self.combos[category] = combo
            if index:
                body.addWidget(Divider())
            hint = " ".join(p for p in (hint, "Sempre confirma." if sensitive else "") if p)
            row = _setting_row(title, hint, combo)
            body.addWidget(row)
        self.feedback.setText(f"{len(categories)} categorias.")

    def _changed(self, category: str, value: str | None) -> None:
        service = self._permissions() if self._permissions is not None else None
        if service is None or value is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._save(service, category, value))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _save(self, service: Any, category: str, value: str) -> None:
        from alfred.core.permissions import Policy

        await service.set_policy(category, Policy(value))
        self.feedback.setText("Permissão salva.")


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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(THEME.spacing.md)
        intro = QLabel(
            "Usado como contexto da inteligência artificial. Dê dois cliques para editar."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "hint")
        self.list = QListWidget()
        self.list.itemChanged.connect(self._edited)
        self.empty = EmptyState(
            "Ainda não lembro de nada sobre você",
            'Conte uma preferência ou um fato — por exemplo, "prefiro respostas curtas" — '
            "e eu passo a levar isso em conta.",
            image=avatar_pixmap(84),
        )
        self.new_item = QLineEdit()
        self.new_item.setPlaceholderText("Ex.: Prefiro respostas curtas.")
        add = QPushButton("Lembrar")
        add.setProperty("role", "primary")
        self.new_item.returnPressed.connect(lambda: self._run(self._add()))
        add.clicked.connect(lambda: self._run(self._add()))
        delete = QPushButton("Excluir selecionada")
        delete.setProperty("role", "ghost")
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
        # Sugestões aprendidas das conversas (#120): aprovar (editando, se quiser) ou descartar.
        self.suggestions_title = QLabel("SUGESTÕES DO ALFRED")
        self.suggestions_title.setProperty("role", "overline")
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(140)
        approve = QPushButton("Aprovar")
        approve.setProperty("role", "primary")
        approve.clicked.connect(lambda: self._run(self._approve()))
        discard = QPushButton("Descartar")
        discard.setProperty("role", "ghost")
        discard.clicked.connect(lambda: self._run(self._discard()))
        self.suggestion_actions = QWidget()
        decide = QHBoxLayout(self.suggestion_actions)
        decide.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Dê dois cliques para corrigir o texto antes de aprovar.")
        hint.setProperty("role", "hint")
        decide.addWidget(hint, 1)
        decide.addWidget(discard)
        decide.addWidget(approve)
        row = QHBoxLayout()
        row.addWidget(self.new_item, 1)
        row.addWidget(add)
        actions = QHBoxLayout()
        actions.addWidget(delete)
        actions.addWidget(self.new_chat)
        actions.addStretch()
        actions.addWidget(forget)
        for widget in (intro, self.list, self.empty):
            layout.addWidget(widget, 1 if widget is not intro else 0)
        self.empty.hide()
        parts: tuple[QWidget, ...] = (
            self.suggestions_title,
            self.suggestions,
            self.suggestion_actions,
        )
        for part in parts:
            layout.addWidget(part)
            part.hide()
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
        self.suggestions.clear()
        pending = await service.suggestions() if hasattr(service, "suggestions") else []
        for suggestion in pending:
            confidence = f"{suggestion.confidence:.0%}" if suggestion.confidence else ""
            entry = QListWidgetItem(suggestion.content)
            entry.setData(Qt.ItemDataRole.UserRole, suggestion.id)
            entry.setToolTip(f"Aprendido de uma conversa · confiança {confidence}")
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsEditable)
            self.suggestions.addItem(entry)
        if pending:
            self.suggestions.setCurrentRow(0)
        for part in (self.suggestions_title, self.suggestions, self.suggestion_actions):
            part.setVisible(bool(pending))
        has_items = self.list.count() > 0
        self.list.setVisible(has_items)
        self.empty.setVisible(not has_items)  # estado vazio com orientação (mascote)
        self.feedback.setText(f"{self.list.count()} memória(s)." if has_items else "")

    async def _approve(self) -> None:
        item = self.suggestions.currentItem()
        service = self._service()
        if item is not None and service is not None:
            await service.approve(int(item.data(Qt.ItemDataRole.UserRole)), item.text())
            await self.reload()

    async def _discard(self) -> None:
        item = self.suggestions.currentItem()
        service = self._service()
        if item is not None and service is not None:
            await service.discard(int(item.data(Qt.ItemDataRole.UserRole)))
            await self.reload()

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


class ApplicationsSection(QWidget):
    """O que o ALFRED aprendeu sobre cada programa (skill "Aprender os programas", #125)."""

    def __init__(self, skills: Callable[[], Any] | None) -> None:
        super().__init__()
        self._skills = skills
        self._pending: set[asyncio.Task[None]] = set()
        self._programs: list[Any] = []
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Programas que o ALFRED já explorou (somente leitura) e procedimentos que você "
            'ensinou. Peça "Alfred, aprenda o Word" com o programa aberto para começar.'
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "muted")
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        forget = QPushButton("Esquecer programa")
        forget.setProperty("role", "danger")
        forget.clicked.connect(lambda: self._run(self._forget()))
        self.feedback = QLabel()
        self.feedback.setProperty("role", "muted")
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(forget)
        for widget in (intro, self.list, self.details):
            layout.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(self.feedback)
        self._run(self.reload())

    def _skill(self) -> Any:
        manager = self._skills() if self._skills is not None else None
        if manager is None:
            return None
        try:
            return manager.get("programas")
        except KeyError:
            return None

    def _run(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def reload(self) -> None:
        skill = self._skill()
        if skill is None:
            self.feedback.setText('A skill "Aprender os programas" não está disponível.')
            return
        self._programs = await skill.programs()
        self.list.clear()
        for knowledge in self._programs:
            self.list.addItem(f"{knowledge.title or knowledge.app_key}  ({knowledge.app_key})")
        self.details.clear()
        self.feedback.setText(
            "Nenhum programa aprendido ainda."
            if not self._programs
            else f"{len(self._programs)} programa(s) aprendido(s)."
        )

    def _show(self, row: int) -> None:
        if not 0 <= row < len(self._programs):
            self.details.clear()
            return
        k = self._programs[row]
        lines = [
            f"Elementos conhecidos: {len(k.elements)}",
            "Atalhos: " + (", ".join(k.shortcuts) or "nenhum"),
            "Procedimentos:",
        ]
        lines += [f"  • {name}: {steps}" for name, steps in sorted(k.procedures.items())] or [
            "  (nenhum)"
        ]
        self.details.setPlainText("\n".join(lines))

    async def _forget(self) -> None:
        skill = self._skill()
        row = self.list.currentRow()
        if skill is None or not 0 <= row < len(self._programs):
            return
        await skill.forget(self._programs[row].app_key)
        await self.reload()


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


class ProfilePage(QWidget):
    """Perfil (#121): o formulário e um botão para voltar tudo ao padrão."""

    def __init__(self, form: SectionForm, on_reset: Callable[[], None]) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(form)
        hint = QLabel(
            'Também dá para ajustar falando: "respostas mais curtas", "seja mais formal", '
            '"me chame de ...", "não faça mais isso" ou "redefinir meu perfil".'
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        self.reset_button = QPushButton("Redefinir perfil")
        self.reset_button.setProperty("role", "ghost")
        self.reset_button.clicked.connect(on_reset)
        row = QHBoxLayout()
        row.addWidget(hint, 1)
        row.addWidget(self.reset_button)
        layout.addLayout(row)
        layout.addStretch()


class PrivacyPage(QWidget):
    def __init__(self, form: SectionForm, overview: PrivacyOverview) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(overview)
        layout.addWidget(form, 1)


class GlowPanel(QWidget):
    """Fundo preto profundo com um brilho dourado muito suave no alto (profundidade, sem neon)."""

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), THEME.color("void"))
        glow = QRadialGradient(
            QPointF(self.width() * 0.85, -self.height() * 0.15), self.width() * 0.7
        )
        glow.setColorAt(0.0, THEME.color("gold", 26))
        glow.setColorAt(1.0, THEME.color("gold", 0))
        painter.fillRect(self.rect(), glow)


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
        permissions: Callable[[], Any] | None = None,
        tools: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("ALFRED — Configurações")
        self.setWindowIcon(alfred_icon())
        self.resize(1040, 720)
        self.setMinimumSize(860, 560)
        self._settings = settings
        self.forms: dict[str, SectionForm] = {}
        self._pending: set[asyncio.Task[bool]] = set()
        self._keys: list[str] = []
        s = THEME.spacing

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------------------------------------------------------------- barra lateral
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(272)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(s.lg, s.xl, s.lg, s.lg)
        side.setSpacing(s.lg)
        brand = QHBoxLayout()
        brand.setSpacing(s.md)
        avatar = QLabel()
        avatar.setPixmap(avatar_pixmap(44))
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel("ALFRED")
        name.setProperty("role", "brand")
        motto = QLabel("Sempre a seu serviço")
        motto.setProperty("role", "hint")
        names.addWidget(name)
        names.addWidget(motto)
        brand.addWidget(avatar)
        brand.addLayout(names, 1)
        side.addLayout(brand)
        self.sections = QListWidget()
        self.sections.setObjectName("sections")
        self.sections.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side.addWidget(self.sections, 1)

        # ---------------------------------------------------------------- páginas
        self.pages = QStackedWidget()
        group = None
        for key, title, phase in SECTIONS:
            section_group = SECTION_INFO.get(key, ("", ""))[0]
            if section_group != group:
                group = section_group
                header = QListWidgetItem(group.upper())
                header.setFlags(Qt.ItemFlag.NoItemFlags)  # rótulo de grupo, não selecionável
                self.sections.addItem(header)
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
            elif key == "applications":
                page = ApplicationsSection(skills)
            elif key == "permissions":
                page = PermissionsSection(permissions, tools)
            elif key == "profile":
                form = SectionForm(key, settings.current, choices)
                self.forms[key] = form
                page = ProfilePage(form, self._reset_profile)
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
            self._keys.append(key)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setViewportMargins(0, 0, s.md, 0)  # barra de rolagem fora do cartão
            scroll.setWidget(page)
            self.pages.addWidget(scroll)

        content = GlowPanel()
        right = QVBoxLayout(content)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        page_area = QVBoxLayout()
        page_area.setContentsMargins(s.xxl + s.sm, s.xxl, s.xxl + s.sm, s.lg)
        page_area.setSpacing(s.lg)
        self.header = SectionHeader("", "", overline=" ")
        page_area.addWidget(self.header)
        page_area.addWidget(self.pages, 1)
        right.addLayout(page_area, 1)

        actionbar = QFrame()
        actionbar.setObjectName("actionbar")
        self.actionbar = actionbar
        bar = QHBoxLayout(actionbar)
        bar.setContentsMargins(s.xxl + s.sm, s.md, s.xxl + s.sm, s.md)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.revert_button = QPushButton("Desfazer alterações")
        self.revert_button.setProperty("role", "ghost")
        self.apply_button = QPushButton("Aplicar")
        self.apply_button.setProperty("role", "primary")
        self.apply_button.setMinimumWidth(120)
        self.revert_button.clicked.connect(self.reload)
        self.apply_button.clicked.connect(self._apply_clicked)
        bar.addWidget(self.feedback, 1)
        bar.addWidget(self.revert_button)
        bar.addWidget(self.apply_button)
        right.addWidget(actionbar)

        root.addWidget(sidebar)
        root.addWidget(content, 1)
        self.sections.currentRowChanged.connect(self._on_row)
        self.select(SECTIONS[0][0])

    def _on_row(self, row: int) -> None:
        item = self.sections.item(row)
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if key is None:
            return
        index = self._keys.index(key)
        self.pages.setCurrentIndex(index)
        group, description = SECTION_INFO.get(key, ("", ""))
        title = next(t for k, t, _ in SECTIONS if k == key)
        overline = self.header.findChild(QLabel)
        if overline is not None and overline is not self.header.title:
            overline.setText(group.upper())
            overline.setVisible(group.casefold() != title.casefold())  # sem "VOZ / Voz"
        self.header.title.setText(title)
        self.header.subtitle.setText(description)
        self.header.subtitle.setVisible(bool(description))
        # Aplicar/Desfazer só onde há formulário; as outras páginas salvam sozinhas.
        self.actionbar.setVisible(key in self.forms)
        current = self.pages.currentWidget()
        if current is not None:
            fade_in(current)

    def select(self, key: str) -> None:
        for row in range(self.sections.count()):
            if self.sections.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.sections.setCurrentRow(row)
                self._on_row(row)

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

    def _reset_profile(self) -> None:
        from alfred.config.settings import ProfileSettings

        async def reset() -> bool:
            await self._settings.update("profile", **ProfileSettings().model_dump())
            self.forms["profile"].load(self._settings.current)
            self._set_feedback("Perfil redefinido para o padrão.", error=False)
            return True

        try:
            task = asyncio.get_running_loop().create_task(reset())
        except RuntimeError:
            return
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

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
        permissions: Callable[[], Any] | None = None,
        tools: Callable[[], Any] | None = None,
    ) -> None:
        self._memory = memory
        self._triggers = triggers
        self._ai_providers = ai_providers
        self._skills = skills
        self._permissions = permissions
        self._tools = tools
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

    def show(self, section: str | None = None) -> None:
        if self.window is None:
            self.window = SettingsWindow(
                self._settings,
                on_open_panel=self._on_open_panel,
                choices=self._choices,
                memory=self._memory,
                triggers=self._triggers,
                ai_providers=self._ai_providers,
                skills=self._skills,
                permissions=self._permissions,
                tools=self._tools,
            )
        else:
            self.window.reload()
        if section is not None:
            self.window.select(section)
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
