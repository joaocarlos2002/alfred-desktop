"""Gera os assets da interface a partir da ilustração original em docs/images.

Uso: uv run python scripts/build_assets.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "images" / "Mordomo Elegante com Cloche e Tablet.png"
OUT = ROOT / "src" / "alfred" / "ui" / "assets"
AVATAR_CROP = QRect(430, 0, 440, 440)  # rosto, na imagem original de 1254 x 1254


def main() -> int:
    QGuiApplication(sys.argv[:1])
    image = QImage(str(SOURCE))
    if image.isNull():
        print(f"Imagem não encontrada: {SOURCE}", file=sys.stderr)
        return 1
    smooth = Qt.TransformationMode.SmoothTransformation
    OUT.mkdir(parents=True, exist_ok=True)
    image.scaled(360, 360, Qt.AspectRatioMode.KeepAspectRatio, smooth).save(
        str(OUT / "butler.png"), "PNG"
    )
    image.copy(AVATAR_CROP).scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio, smooth).save(
        str(OUT / "butler_avatar.png"), "PNG"
    )
    for path in sorted(OUT.glob("*.png")):
        print(f"{path.name}: {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
