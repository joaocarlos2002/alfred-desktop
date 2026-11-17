"""Gera o instalador do ALFRED (#49) a partir de ``dist/ALFRED`` (``scripts/build.py``).

Uso: ``uv run python scripts/build_installer.py``

Procura o compilador do Inno Setup 6 (``ISCC.exe``) no PATH e nos locais padrão e gera
``dist/ALFRED-Setup-<versão>.exe``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "installer.iss"


def find_iscc() -> Path:
    found = shutil.which("ISCC") or shutil.which("iscc")
    candidates = [Path(found)] if found else []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
            candidates.append(Path(base) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit(
        "Inno Setup 6 (ISCC.exe) não encontrado. Instale: winget install JRSoftware.InnoSetup"
    )


def main() -> int:
    from alfred import __version__

    if not (ROOT / "dist" / "ALFRED" / "alfred.exe").exists():
        raise SystemExit("dist/ALFRED/alfred.exe não existe: rode scripts/build.py antes.")
    if not (ROOT / "packaging" / "alfred.ico").exists():
        raise SystemExit("packaging/alfred.ico não existe: rode scripts/build.py antes.")
    subprocess.run(
        [str(find_iscc()), f"/DAppVersion={__version__}", str(SCRIPT)], check=True, cwd=ROOT
    )
    output = ROOT / "dist" / f"ALFRED-Setup-{__version__}.exe"
    print(f"{output.relative_to(ROOT)} ({output.stat().st_size / 2**20:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
