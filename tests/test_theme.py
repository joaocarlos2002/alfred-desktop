import re
from dataclasses import fields
from pathlib import Path

import alfred.ui
from alfred.ui.qt_runtime import ensure_qt_application
from alfred.ui.theme import THEME, Colors, build_stylesheet

HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\b")
UI_DIR = Path(alfred.ui.__file__).parent


def test_no_hardcoded_colors_outside_theme() -> None:
    offenders = [
        f"{path.name}: {match}"
        for path in UI_DIR.rglob("*.py")
        if path.name != "theme.py"
        for match in HEX_COLOR.findall(path.read_text("utf-8"))
    ]
    assert offenders == []


def test_stylesheet_uses_tokens() -> None:
    qss = build_stylesheet()
    for name in ("obsidian", "graphite", "slate", "accent", "text"):
        assert getattr(THEME.colors, name) in qss


def test_all_colors_are_valid() -> None:
    for item in fields(Colors):
        assert THEME.color(item.name).isValid(), item.name


def test_theme_is_applied_to_application() -> None:
    app = ensure_qt_application()
    assert THEME.colors.obsidian in app.styleSheet()
