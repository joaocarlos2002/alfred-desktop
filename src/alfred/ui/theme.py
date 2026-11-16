"""Design system do ALFRED: tokens visuais e stylesheet Qt.

Fonte única de cores, tipografia, espaçamento, raios e movimento. Nenhum componente define cores
próprias: todos leem daqui (verificado por teste).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    obsidian: str = "#0B0D10"
    """Fundo principal."""
    graphite: str = "#14171C"
    """Superfícies elevadas (painéis, widget)."""
    slate: str = "#1F242B"
    """Bordas, divisores, campos."""
    steel: str = "#2C333C"
    """Hover e estados ativos de superfície."""
    text: str = "#E6E8EB"
    text_muted: str = "#8B939E"
    accent: str = "#C9A227"
    """Dourado discreto: foco, seleção, identidade."""
    accent_soft: str = "#3A3220"
    listening: str = "#4FA3FF"
    processing: str = "#C9A227"
    executing: str = "#7C5CFF"
    confirm: str = "#F2A541"
    success: str = "#3FB97A"
    error: str = "#E5484D"


@dataclass(frozen=True)
class Typography:
    family: str = "Segoe UI Variable"
    fallback: str = "Segoe UI"
    size_small: int = 9
    size_body: int = 10
    size_title: int = 13
    weight_title: int = 600


@dataclass(frozen=True)
class Spacing:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24


@dataclass(frozen=True)
class Radius:
    sm: int = 6
    md: int = 10
    lg: int = 16


@dataclass(frozen=True)
class Motion:
    fast_ms: int = 120
    normal_ms: int = 220
    slow_ms: int = 400
    pulse_ms: int = 1400
    """Ciclo das animações de estado (ouvindo, processando, executando)."""
    fps: int = 60


@dataclass(frozen=True)
class Theme:
    colors: Colors = field(default_factory=Colors)
    typography: Typography = field(default_factory=Typography)
    spacing: Spacing = field(default_factory=Spacing)
    radius: Radius = field(default_factory=Radius)
    motion: Motion = field(default_factory=Motion)

    def color(self, name: str, alpha: int = 255) -> QColor:
        color = QColor(getattr(self.colors, name))
        color.setAlpha(alpha)
        return color

    def font(self, size: int | None = None, *, bold: bool = False) -> QFont:
        font = QFont()
        font.setFamilies([self.typography.family, self.typography.fallback])
        font.setPointSize(size or self.typography.size_body)
        if bold:
            font.setWeight(QFont.Weight(self.typography.weight_title))
        return font


THEME = Theme()


def build_stylesheet(theme: Theme = THEME) -> str:
    c, s, r, t = theme.colors, theme.spacing, theme.radius, theme.typography
    return f"""
QWidget {{
    background-color: {c.obsidian};
    color: {c.text};
    font-family: "{t.family}", "{t.fallback}";
    font-size: {t.size_body}pt;
}}
QMenu {{
    background-color: {c.graphite};
    border: 1px solid {c.slate};
    border-radius: {r.sm}px;
    padding: {s.xs}px;
}}
QMenu::item {{ padding: {s.xs + 2}px {s.lg}px; border-radius: {r.sm - 2}px; }}
QMenu::item:selected {{ background-color: {c.steel}; }}
QMenu::item:disabled {{ color: {c.text_muted}; }}
QMenu::separator {{ height: 1px; background: {c.slate}; margin: {s.xs}px {s.sm}px; }}
QPushButton {{
    background-color: {c.slate};
    border: 1px solid {c.steel};
    border-radius: {r.sm}px;
    padding: {s.xs + 2}px {s.md}px;
}}
QPushButton:hover {{ background-color: {c.steel}; }}
QPushButton:focus {{ border-color: {c.accent}; }}
QPushButton:disabled {{ color: {c.text_muted}; }}
QPushButton[role="primary"] {{
    background-color: {c.accent_soft}; border-color: {c.accent}; color: {c.accent};
}}
QPushButton[role="danger"] {{ border-color: {c.error}; color: {c.error}; }}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox, QTreeWidget, QListWidget {{
    background-color: {c.graphite};
    border: 1px solid {c.slate};
    border-radius: {r.sm}px;
    padding: {s.xs}px;
    selection-background-color: {c.accent_soft};
    selection-color: {c.text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {c.accent};
}}
QHeaderView::section {{
    background-color: {c.graphite};
    color: {c.text_muted};
    border: none;
    border-bottom: 1px solid {c.slate};
    padding: {s.xs}px {s.sm}px;
}}
QTabWidget::pane {{ border: 1px solid {c.slate}; border-radius: {r.sm}px; }}
QTabBar::tab {{ background: transparent; color: {c.text_muted}; padding: {s.sm}px {s.md}px; }}
QTabBar::tab:selected {{ color: {c.accent}; border-bottom: 2px solid {c.accent}; }}
QListWidget#sections::item {{ padding: {s.sm}px {s.md}px; border-radius: {r.sm}px; }}
QListWidget#sections::item:selected {{ background-color: {c.steel}; color: {c.accent}; }}
QLabel[role="title"] {{ font-size: {t.size_title}pt; font-weight: {t.weight_title}; }}
QLabel[role="muted"] {{ color: {c.text_muted}; }}
QLabel[role="error"] {{ color: {c.error}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {c.steel}; border-radius: 3px;
}}
QCheckBox::indicator:checked {{ background-color: {c.accent}; border-color: {c.accent}; }}
"""


def apply_theme(app: QApplication, theme: Theme = THEME) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, theme.color("obsidian"))
    palette.setColor(QPalette.ColorRole.Base, theme.color("graphite"))
    palette.setColor(QPalette.ColorRole.Text, theme.color("text"))
    palette.setColor(QPalette.ColorRole.WindowText, theme.color("text"))
    palette.setColor(QPalette.ColorRole.Highlight, theme.color("accent_soft"))
    palette.setColor(QPalette.ColorRole.Button, theme.color("slate"))
    palette.setColor(QPalette.ColorRole.ButtonText, theme.color("text"))
    app.setPalette(palette)
    app.setFont(theme.font())
    app.setStyleSheet(build_stylesheet(theme))
