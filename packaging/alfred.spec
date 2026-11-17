# -*- mode: python ; coding: utf-8 -*-
# Build reproduzível do ALFRED (issue #48): ``uv run python scripts/build.py``.
#
# Modo "onedir" (pasta com alfred.exe): inicia mais rápido que "onefile" (sem extrair para %TEMP%
# a cada execução) e gera menos falsos positivos de antivírus. Modelos de voz e de IA NÃO entram
# no pacote: são baixados sob demanda para %LOCALAPPDATA%\ALFRED\models.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - variável do PyInstaller
SRC = ROOT / "src"

datas = [
    (str(SRC / "alfred" / "ui" / "assets"), "alfred/ui/assets"),
    (str(SRC / "alfred" / "infrastructure" / "db" / "migrations"), "alfred/infrastructure/db/migrations"),
]
datas += collect_data_files("faster_whisper")  # VAD (silero) usado pela transcrição
datas += collect_data_files("piper")  # dados do espeak-ng
datas += collect_data_files("playwright")  # driver (usa o Edge do sistema, sem navegadores)
binaries = collect_dynamic_libs("vosk") + collect_dynamic_libs("ctranslate2")

hiddenimports = (
    collect_submodules("alembic")
    + collect_submodules("alfred")  # imports sob demanda (lazy) e as migrações do Alembic
    + ["keyring.backends.Windows", "win32timezone"]
)

a = Analysis(  # noqa: F821
    [str(SRC / "alfred" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="alfred",
    icon=str(ROOT / "packaging" / "alfred.ico"),
    console=False,  # app de bandeja; ``--version``/``--headless`` gravam no log
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # UPX aumenta falsos positivos de antivírus
    name="ALFRED",
)
