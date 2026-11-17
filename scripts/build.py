"""Gera o executável distribuível do ALFRED (issue #48) e faz um teste de fumaça.

Uso: ``uv run --group build python scripts/build.py [--skip-smoke]``

1. Gera ``packaging/alfred.ico`` a partir do ícone desenhado em ``alfred.ui.icon``.
2. Roda o PyInstaller com ``packaging/alfred.spec`` (modo pasta, sem UPX) → ``dist/ALFRED``.
3. Teste de fumaça: inicia ``dist/ALFRED/alfred.exe --headless`` com diretório de dados vazio,
   espera "ALFRED em execução" no log (banco criado e migrado) e encerra.
4. Compacta em ``dist/ALFRED-<versão>-win64.zip`` e imprime tamanho e tempo de inicialização.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "alfred.spec"
ICON = ROOT / "packaging" / "alfred.ico"
DIST = ROOT / "dist"
APP_DIR = DIST / "ALFRED"
READY_MARKER = "ALFRED em execu"


def make_icon() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    from alfred.ui.icon import render_icon_pixmap

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    if not render_icon_pixmap(256).toImage().save(str(ICON), "ICO"):
        raise RuntimeError("Não foi possível gerar o ícone .ico")
    del app


def run_pyinstaller() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(SPEC),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(ROOT / "build"),
        ],
        check=True,
        cwd=ROOT,
    )


def smoke_test(timeout: float = 60.0) -> float:
    exe = APP_DIR / "alfred.exe"
    with tempfile.TemporaryDirectory(prefix="alfred-smoke-", ignore_cleanup_errors=True) as tmp:
        env = {**os.environ, "ALFRED_DATA_DIR": tmp}
        env.pop("QT_QPA_PLATFORM", None)
        started = time.perf_counter()
        process = subprocess.Popen([str(exe), "--headless"], env=env)
        log = Path(tmp) / "logs" / "alfred.log"
        try:
            while time.perf_counter() - started < timeout:
                if process.poll() is not None:
                    raise RuntimeError(f"alfred.exe terminou com código {process.returncode}")
                if log.exists() and READY_MARKER in log.read_text("utf-8", errors="replace"):
                    elapsed = time.perf_counter() - started
                    if not (Path(tmp) / "alfred.db").exists():
                        raise RuntimeError("banco local não foi criado")
                    return elapsed
                time.sleep(0.1)
            raise TimeoutError("alfred.exe não ficou pronto a tempo")
        finally:
            process.terminate()
            process.wait(timeout=15)


def self_check() -> dict[str, object]:
    """Roda ``alfred.exe --self-check`` (componentes nativos dentro do pacote)."""
    exe = APP_DIR / "alfred.exe"
    with tempfile.TemporaryDirectory(prefix="alfred-check-", ignore_cleanup_errors=True) as tmp:
        output = Path(tmp) / "check.json"
        env = {**os.environ, "ALFRED_DATA_DIR": tmp}
        code = subprocess.run(
            [str(exe), "--self-check", "--self-check-output", str(output)], env=env, timeout=180
        ).returncode
        data: dict[str, object] = json.loads(output.read_text("utf-8"))
    failed = [c["name"] for c in data["checks"] if c["required"] and not c["ok"]]  # type: ignore[attr-defined, index]
    if code != 0 or failed:
        raise RuntimeError(f"--self-check falhou no executável: {failed}")
    return data


def package(version: str) -> Path:
    archive = DIST / f"ALFRED-{version}-win64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in APP_DIR.rglob("*"):
            zf.write(path, Path("ALFRED") / path.relative_to(APP_DIR))
    return archive


def folder_mb(path: Path) -> float:
    return round(sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 2**20, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args()
    from alfred import __version__

    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    built = time.perf_counter()
    make_icon()
    run_pyinstaller()
    build_s = time.perf_counter() - built
    startup = None if args.skip_smoke else round(smoke_test(), 2)
    checks = None if args.skip_smoke else self_check()
    archive = package(__version__)
    print(
        json.dumps(
            {
                "version": __version__,
                "build_seconds": round(build_s, 1),
                "folder_mb": folder_mb(APP_DIR),
                "zip_mb": round(archive.stat().st_size / 2**20, 1),
                "smoke_startup_seconds": startup,
                "self_check": None
                if checks is None
                else {c["name"]: c["ok"] for c in checks["checks"]},  # type: ignore[attr-defined]
                "artifact": str(archive.relative_to(ROOT)),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
