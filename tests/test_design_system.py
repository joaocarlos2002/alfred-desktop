"""Design system v2 (#111): paleta, contraste, tokens, movimento reduzido e componentes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

import alfred.ui
from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt
from alfred.ui.theme import EASINGS, THEME, build_stylesheet

UI_DIR = Path(alfred.ui.__file__).parent
TOKEN_FILES = {"theme.py", "motion.py"}


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return ensure_qt_application()


def test_identity_palette_is_exact() -> None:
    c = THEME.colors
    assert (c.void, c.graphite, c.charcoal, c.silver, c.ivory, c.gold) == (
        "#08090B",
        "#151519",
        "#242429",
        "#BFC1C6",
        "#F2F0EC",
        "#B69A78",
    )
    assert c.obsidian == c.void
    assert c.accent == c.gold
    assert c.text == c.ivory


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize(
    ("fg", "bg", "minimum"),
    [
        ("text", "obsidian", 4.5),
        ("text", "graphite", 4.5),
        ("text", "surface_2", 4.5),
        ("text", "steel", 4.5),
        ("text_secondary", "graphite", 4.5),
        ("text_muted", "graphite", 4.5),
        ("text_muted", "obsidian", 4.5),
        ("accent", "obsidian", 4.5),
        ("accent_strong", "accent_soft", 4.5),
        ("void", "accent", 4.5),  # texto do botão primário em hover
        ("error", "graphite", 4.5),
        ("success", "graphite", 4.5),
        ("listening", "graphite", 3.0),  # indicadores gráficos: 3:1 (WCAG 1.4.11)
        ("executing", "graphite", 3.0),
        ("confirm", "graphite", 3.0),
    ],
)
def test_contrast_meets_wcag_aa(fg: str, bg: str, minimum: float) -> None:
    colors = THEME.colors
    ratio = contrast(getattr(colors, fg), getattr(colors, bg))
    assert ratio >= minimum, f"{fg} sobre {bg}: {ratio:.2f}:1"


def _ui_sources() -> list[tuple[str, str]]:
    return [
        (path.name, path.read_text("utf-8"))
        for path in UI_DIR.rglob("*.py")
        if path.name not in TOKEN_FILES
    ]


def test_no_literal_colors_durations_or_easings_outside_tokens() -> None:
    rules = {
        "cor hex": re.compile(r"#[0-9A-Fa-f]{6}\b"),
        "cor rgb": re.compile(r"QColor\(\s*\d"),
        "duração": re.compile(r"setDuration\(\s*\d"),
        "intervalo": re.compile(r"setInterval\(\s*\d"),
        "easing": re.compile(r"QEasingCurve\.Type\."),
    }
    offenders = [
        f"{name}: {kind}"
        for name, text in _ui_sources()
        for kind, rule in rules.items()
        if rule.search(text)
    ]
    assert offenders == []


def test_stylesheet_and_motion_tokens() -> None:
    qss = build_stylesheet()
    for token in ("void", "graphite", "charcoal", "gold", "ivory", "silver"):
        assert getattr(THEME.colors, token) in qss
    for role in ('role="primary"', 'role="ghost"', 'role="card"', 'role="chip"', "QToolTip"):
        assert role in qss
    assert set(EASINGS) >= {"standard", "enter", "exit", "emphasis", "pulse"}
    assert THEME.motion.fast_ms < THEME.motion.normal_ms < THEME.motion.slow_ms


def test_reduced_motion_from_user_or_windows() -> None:
    from alfred.ui.motion import MotionPolicy

    system_reduced = MotionPolicy(system=lambda: True)
    system_reduced.configure(animations_enabled=True)
    assert system_reduced.reduced
    assert system_reduced.duration("normal") == 0

    user_off = MotionPolicy(system=lambda: False)
    user_off.configure(animations_enabled=False)
    assert user_off.reduced

    full = MotionPolicy(system=lambda: False)
    full.configure(animations_enabled=True)
    assert not full.reduced
    assert full.duration("normal") == THEME.motion.normal_ms


def test_windows_preference_is_readable() -> None:
    from alfred.ui.motion import system_prefers_reduced_motion

    assert isinstance(system_prefers_reduced_motion(), bool)


def test_toggle_animates_and_jumps_with_reduced_motion(qt_app: QApplication) -> None:
    from alfred.ui import components
    from alfred.ui.motion import MotionPolicy

    original = components.MOTION
    try:
        components.MOTION = MotionPolicy(system=lambda: False)
        toggle = components.Toggle()

        async def animated() -> float:
            import asyncio

            toggle.setChecked(True)
            mid = toggle.offset
            await asyncio.sleep(THEME.motion.fast_ms / 1000 + 0.15)
            assert mid < 1.0  # começou do início, não saltou
            return float(toggle.offset)

        assert run_on_qt(animated()) == pytest.approx(1.0)

        components.MOTION = MotionPolicy(system=lambda: True)
        reduced = components.Toggle()
        reduced.setChecked(True)
        assert reduced.offset == 1.0  # salto imediato, sem animação
    finally:
        components.MOTION = original


def test_components_render_with_tokens(qt_app: QApplication) -> None:
    from alfred.ui.components import Card, Chip, EmptyState, SectionHeader, StatusDot, Toast

    card = Card(level=2)
    assert card.property("role") == "card"
    chip = Chip("Local", tone="accent")
    chip.set_tone("success")
    assert chip.property("tone") == "success"
    header = SectionHeader("Memória", "O que o ALFRED lembra", overline="Inteligência")
    assert header.title.text() == "Memória"
    clicked: list[bool] = []
    empty = EmptyState("Nada por aqui", "Peça algo ao ALFRED.", action="Começar")
    empty.action_clicked.connect(lambda: clicked.append(True))
    assert empty.button is not None
    empty.button.click()
    assert clicked == [True]
    dot = StatusDot("listening")
    dot.set_active(True)
    dot.set_active(False)
    assert not dot.pulsing
    host = QWidget()
    host.resize(400, 300)
    toast = Toast(host, "Configurações salvas.", tone="success", timeout_ms=10)
    toast.show_animated()
    assert toast.isVisibleTo(host)
    for widget in (card, chip, header, empty, dot):
        widget.grab()  # pinta de verdade (sem exceções)
