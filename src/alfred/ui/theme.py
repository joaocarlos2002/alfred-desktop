"""Design system v2 do ALFRED (#111): tokens visuais, movimento e stylesheet Qt.

Identidade: preto profundo, grafite e carvão em camadas, prata e branco suave no texto e um único
acento — dourado discreto. Profundidade vem de superfícies sobrepostas, bordas sutis e brilho
controlado, nunca de gradientes chamativos.

Fonte única de cores, tipografia, espaçamento, raios, elevação e movimento: nenhum componente
define valores próprios (verificado por teste).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    # ---------------------------------------------------------------- paleta da identidade
    void: str = "#08090B"
    """Preto profundo."""
    graphite: str = "#151519"
    """Grafite: superfícies elevadas (painéis, widget, cartões)."""
    charcoal: str = "#242429"
    """Cinza carvão: bordas, divisores, campos."""
    silver: str = "#BFC1C6"
    """Prata: texto secundário, ícones."""
    ivory: str = "#F2F0EC"
    """Branco suave: texto principal."""
    gold: str = "#B69A78"
    """Dourado discreto: o único acento (foco, seleção, identidade)."""

    # ---------------------------------------------------------------- papéis (derivados)
    obsidian: str = "#08090B"
    """Fundo principal (= void)."""
    surface_2: str = "#1B1B20"
    """Superfície sobre o grafite (cartões dentro de painéis)."""
    slate: str = "#242429"
    """Bordas e campos (= charcoal)."""
    steel: str = "#2F2F36"
    """Hover e estados ativos de superfície."""
    border_soft: str = "#1E1E23"
    """Borda quase invisível que separa camadas."""
    highlight: str = "#2D2D34"
    """Aresta superior iluminada dos cartões (luz vinda de cima)."""
    """Borda quase invisível que separa camadas."""
    text: str = "#F2F0EC"
    text_secondary: str = "#BFC1C6"
    text_muted: str = "#8C8E95"
    accent: str = "#B69A78"
    accent_strong: str = "#CDB291"
    """Dourado mais claro: hover do acento, brilho."""
    accent_soft: str = "#2A241D"
    """Fundo tingido de dourado (seleção, botão primário)."""

    # ---------------------------------------------------------------- estados do assistente
    listening: str = "#9FB4CC"
    processing: str = "#B69A78"
    executing: str = "#D7C3A0"
    confirm: str = "#D9A55B"
    success: str = "#8DB596"
    error: str = "#D06A6A"


@dataclass(frozen=True)
class Typography:
    family: str = "Segoe UI Variable Text"
    display_family: str = "Segoe UI Variable Display"
    fallback: str = "Segoe UI"
    size_caption: float = 8.0
    size_small: float = 9.0
    size_body: float = 10.5
    size_subtitle: float = 12.0
    size_title: float = 15.0
    size_display: float = 22.0
    weight_regular: int = 400
    weight_medium: int = 500
    weight_title: int = 600
    tracking_overline: float = 1.2
    """Espaçamento entre letras (pt) dos rótulos em caixa alta."""


@dataclass(frozen=True)
class Spacing:
    xxs: int = 2
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32
    xxxl: int = 48


@dataclass(frozen=True)
class Radius:
    sm: int = 6
    md: int = 10
    lg: int = 14
    xl: int = 20
    pill: int = 11
    """Pílula para elementos de ~22 px de altura (no QSS, raio > metade da altura é ignorado)."""


@dataclass(frozen=True)
class Shadow:
    blur: int
    offset_y: int
    alpha: int


@dataclass(frozen=True)
class Elevation:
    """Sombras suaves e brilho controlado (nunca halos coloridos exagerados)."""

    low: Shadow = field(default_factory=lambda: Shadow(blur=12, offset_y=2, alpha=110))
    medium: Shadow = field(default_factory=lambda: Shadow(blur=24, offset_y=6, alpha=140))
    high: Shadow = field(default_factory=lambda: Shadow(blur=40, offset_y=12, alpha=170))
    glow_alpha: int = 70
    """Opacidade do brilho dourado em elementos em foco/ativos."""


EASINGS: dict[str, QEasingCurve.Type] = {
    "standard": QEasingCurve.Type.OutCubic,
    "enter": QEasingCurve.Type.OutQuint,
    "exit": QEasingCurve.Type.InCubic,
    "emphasis": QEasingCurve.Type.OutBack,
    "pulse": QEasingCurve.Type.InOutSine,
    "linear": QEasingCurve.Type.Linear,
}


@dataclass(frozen=True)
class Motion:
    instant_ms: int = 90
    fast_ms: int = 140
    normal_ms: int = 220
    slow_ms: int = 360
    emphasis_ms: int = 480
    pulse_ms: int = 1400
    """Ciclo das animações de estado (ouvindo, processando, executando)."""
    poll_ms: int = 100
    """Atualização de elementos vivos (destaques, contagens)."""
    ticker_ms: int = 250
    """Contagens regressivas visíveis (ex.: prazo de confirmação)."""
    fps: int = 60

    @property
    def frame_ms(self) -> int:
        """Intervalo entre quadros na taxa-alvo (60 FPS ≈ 16 ms)."""
        return 1000 // self.fps

    def easing(self, name: str) -> QEasingCurve:
        return QEasingCurve(EASINGS[name])


@dataclass(frozen=True)
class Stage:
    """Palco do widget e do painel rápido (#113): vidro escuro, luzes e cores de estado."""

    base: str = "#0D0E12"
    ink: str = "#F2F0EC"
    muted: str = "#9A9CA3"
    sub: str = "#C9CBD1"
    gold: str = "#C9A87C"
    gold_hi: str = "#F1D6A8"
    gold_glow: str = "#FFF0D6"
    gold_text: str = "#141210"
    white: str = "#FFFFFF"
    black: str = "#000000"
    glass: str = "#0C0D11"
    glass_hover: str = "#1E1F26"
    track: str = "#2A2B31"
    knob: str = "#8D8F96"
    knob_on: str = "#111111"
    orb_top: str = "#2A2B31"
    orb_bottom: str = "#0C0D10"
    orb_edge: str = "#0B0C10"
    violet: str = "#785AC8"
    teal: str = "#3C8CAA"
    ok: str = "#7BD88F"
    warn: str = "#FFB054"
    # Cores dos estados do assistente no widget.
    mode_idle: str = "#C9A87C"
    mode_listening: str = "#6ED6C8"
    mode_thinking: str = "#A78BFA"
    mode_executing: str = "#7DB0FF"
    mode_speaking: str = "#F1C478"
    mode_confirm: str = "#FFB054"
    mode_done: str = "#7BD88F"
    mode_error: str = "#F06868"


@dataclass(frozen=True)
class Theme:
    colors: Colors = field(default_factory=Colors)
    typography: Typography = field(default_factory=Typography)
    spacing: Spacing = field(default_factory=Spacing)
    radius: Radius = field(default_factory=Radius)
    elevation: Elevation = field(default_factory=Elevation)
    motion: Motion = field(default_factory=Motion)
    stage: Stage = field(default_factory=Stage)

    def color(self, name: str, alpha: int = 255) -> QColor:
        color = QColor(getattr(self.colors, name))
        color.setAlpha(alpha)
        return color

    def stage_color(self, name: str, alpha: int = 255) -> QColor:
        color = QColor(getattr(self.stage, name))
        color.setAlpha(max(0, min(255, alpha)))
        return color

    def font(
        self,
        size: float | None = None,
        *,
        bold: bool = False,
        weight: int | None = None,
        display: bool = False,
    ) -> QFont:
        t = self.typography
        font = QFont()
        font.setFamilies([t.display_family if display else t.family, t.fallback])
        font.setPointSizeF(size or t.size_body)
        chosen = weight if weight is not None else (t.weight_title if bold else None)
        if chosen is not None:
            font.setWeight(QFont.Weight(chosen))
        return font


THEME = Theme()


def build_stylesheet(theme: Theme = THEME) -> str:
    c, s, r, t = theme.colors, theme.spacing, theme.radius, theme.typography
    return f"""
QWidget {{
    background-color: transparent;
    color: {c.text};
    font-family: "{t.family}", "{t.fallback}";
    font-size: {t.size_body}pt;
}}
QMainWindow, QDialog, QWidget#window {{ background-color: {c.obsidian}; }}
QToolTip {{
    background-color: {c.graphite};
    color: {c.text};
    border: 1px solid {c.slate};
    border-radius: {r.sm}px;
    padding: {s.xs + 2}px {s.sm}px;
}}
QMenu {{
    background-color: {c.graphite};
    border: 1px solid {c.slate};
    border-radius: {r.md}px;
    padding: {s.xs}px;
}}
QMenu::item {{ padding: {s.sm - 1}px {s.lg}px; border-radius: {r.sm}px; color: {c.text}; }}
QMenu::item:selected {{ background-color: {c.steel}; color: {c.accent_strong}; }}
QMenu::item:disabled {{ color: {c.text_muted}; }}
QMenu::separator {{ height: 1px; background: {c.border_soft}; margin: {s.xs}px {s.sm}px; }}
QPushButton {{
    background-color: {c.graphite};
    border: 1px solid {c.slate};
    border-radius: {r.md}px;
    padding: {s.sm - 1}px {s.lg}px;
    color: {c.text};
    font-weight: {t.weight_medium};
}}
QPushButton:hover {{ background-color: {c.steel}; border-color: {c.steel}; }}
QPushButton:pressed {{ background-color: {c.slate}; }}
QPushButton:focus {{ border-color: {c.accent}; }}
QPushButton:disabled {{ color: {c.text_muted}; border-color: {c.border_soft}; }}
QPushButton[role="primary"] {{
    background-color: {c.accent_soft}; border-color: {c.accent}; color: {c.accent_strong};
}}
QPushButton[role="primary"]:hover {{ background-color: {c.accent}; color: {c.void}; }}
QPushButton[role="ghost"] {{ background-color: transparent; border-color: transparent; }}
QPushButton[role="ghost"]:hover {{ background-color: {c.graphite}; }}
QPushButton[role="danger"] {{ border-color: {c.error}; color: {c.error}; }}
QPushButton[role="danger"]:hover {{ background-color: {c.error}; color: {c.void}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {c.graphite};
    border: 1px solid {c.slate};
    border-radius: {r.md}px;
    padding: {s.xs + 2}px {s.sm}px;
    selection-background-color: {c.accent_soft};
    selection-color: {c.text};
}}
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border-color: {c.steel};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus {{
    border-color: {c.accent};
}}
QComboBox QAbstractItemView {{
    background-color: {c.graphite};
    border: 1px solid {c.slate};
    selection-background-color: {c.steel};
    selection-color: {c.accent_strong};
    outline: none;
}}
QTreeWidget, QListWidget, QTableWidget {{
    background-color: {c.graphite};
    border: 1px solid {c.border_soft};
    border-radius: {r.lg}px;
    padding: {s.xs}px;
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{
    padding: {s.sm - 2}px {s.sm}px;
    border-radius: {r.sm}px;
}}
QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {c.surface_2}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {c.steel}; color: {c.accent_strong};
}}
QHeaderView::section {{
    background-color: {c.graphite};
    color: {c.text_muted};
    border: none;
    border-bottom: 1px solid {c.slate};
    padding: {s.xs}px {s.sm}px;
}}
QTabWidget::pane {{ border: 1px solid {c.border_soft}; border-radius: {r.lg}px; }}
QTabBar::tab {{
    background: transparent; color: {c.text_muted}; padding: {s.sm}px {s.lg}px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {c.text}; }}
QTabBar::tab:selected {{ color: {c.accent_strong}; border-bottom: 2px solid {c.accent}; }}
QProgressBar {{
    background-color: {c.graphite}; border: 1px solid {c.border_soft}; border-radius: {r.pill}px;
    height: {s.sm - 2}px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background-color: {c.accent}; border-radius: {r.pill}px; }}
QListWidget#sections {{
    padding: {s.sm}px; background-color: transparent; border: none;
}}
QListWidget#sections::item {{
    padding: {s.xs + 2}px {s.md}px; margin: 1px 0; border-radius: {r.md}px;
    color: {c.text_secondary};
}}
QListWidget#sections::item:hover {{ background-color: {c.surface_2}; color: {c.text}; }}
QListWidget#sections::item:selected {{
    background-color: {c.accent_soft}; color: {c.accent_strong};
}}
QListWidget#sections::item:disabled {{
    color: {c.accent}; font-size: {t.size_caption}pt;
    font-weight: {t.weight_title}; padding-top: {s.md}px; background: transparent;
}}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle {{
    background: {c.slate}; border-radius: 3px; min-height: 24px; min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {c.steel}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel[role="display"] {{
    font-family: "{t.display_family}", "{t.fallback}";
    font-size: {t.size_display}pt; font-weight: {t.weight_title}; color: {c.text};
}}
QLabel[role="title"] {{
    font-family: "{t.display_family}", "{t.fallback}";
    font-size: {t.size_title}pt; font-weight: {t.weight_title};
}}
QLabel[role="subtitle"] {{
    font-size: {t.size_subtitle}pt; font-weight: {t.weight_medium}; color: {c.text};
}}
QLabel[role="overline"] {{
    font-size: {t.size_caption}pt; font-weight: {t.weight_title}; color: {c.accent};
    letter-spacing: {t.tracking_overline}pt;
}}
QLabel[role="muted"] {{ color: {c.text_muted}; }}
QLabel[role="secondary"] {{ color: {c.text_secondary}; }}
QLabel[role="error"] {{ color: {c.error}; }}
QLabel[role="chip"] {{
    background-color: {c.surface_2}; color: {c.text_secondary};
    border: 1px solid {c.slate}; border-radius: {r.pill}px;
    padding: {s.xxs}px {s.sm}px; font-size: {t.size_small}pt;
}}
QLabel[role="chip"][tone="accent"] {{ color: {c.accent_strong}; border-color: {c.accent}; }}
QLabel[role="chip"][tone="success"] {{ color: {c.success}; border-color: {c.success}; }}
QLabel[role="chip"][tone="error"] {{ color: {c.error}; border-color: {c.error}; }}
QFrame[role="card"] {{
    background-color: {c.graphite}; border: 1px solid {c.border_soft};
    border-top: 1px solid {c.highlight}; border-radius: {r.lg}px;
}}
QFrame#sidebar {{ background-color: {c.graphite}; border-right: 1px solid {c.border_soft}; }}
QFrame#actionbar {{
    background-color: {c.graphite}; border-top: 1px solid {c.border_soft};
}}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QLabel[role="brand"] {{
    font-family: "{t.display_family}", "{t.fallback}";
    font-size: {t.size_subtitle + 2}pt; font-weight: {t.weight_title}; color: {c.text};
    letter-spacing: 2pt;
}}
QLabel[role="setting"] {{ font-size: {t.size_body}pt; font-weight: {t.weight_medium}; }}
QLabel[role="hint"] {{ color: {c.text_muted}; font-size: {t.size_small}pt; }}
QFrame[role="card"][level="2"] {{ background-color: {c.surface_2}; }}
QFrame[role="divider"] {{ background-color: {c.border_soft}; max-height: 1px; border: none; }}
QCheckBox {{ spacing: {s.sm}px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {c.steel}; border-radius: 5px;
    background-color: {c.graphite};
}}
QCheckBox::indicator:hover {{ border-color: {c.accent}; }}
QCheckBox::indicator:checked {{ background-color: {c.accent}; border-color: {c.accent}; }}
QDialog {{ background-color: {c.graphite}; }}
"""


def apply_theme(app: QApplication, theme: Theme = THEME) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, theme.color("obsidian"))
    palette.setColor(QPalette.ColorRole.Base, theme.color("graphite"))
    palette.setColor(QPalette.ColorRole.AlternateBase, theme.color("surface_2"))
    palette.setColor(QPalette.ColorRole.Text, theme.color("text"))
    palette.setColor(QPalette.ColorRole.WindowText, theme.color("text"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, theme.color("text_muted"))
    palette.setColor(QPalette.ColorRole.Highlight, theme.color("accent_soft"))
    palette.setColor(QPalette.ColorRole.HighlightedText, theme.color("accent_strong"))
    palette.setColor(QPalette.ColorRole.Button, theme.color("graphite"))
    palette.setColor(QPalette.ColorRole.ButtonText, theme.color("text"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, theme.color("graphite"))
    palette.setColor(QPalette.ColorRole.ToolTipText, theme.color("text"))
    app.setPalette(palette)
    app.setFont(theme.font())
    app.setStyleSheet(build_stylesheet(theme))
