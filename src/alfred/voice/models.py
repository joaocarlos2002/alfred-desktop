"""Download sob demanda de modelos de voz para ``data/models``.

Todo arquivo baixado tem o SHA-256 conferido antes de ser instalado (cadeia de suprimentos,
docs/security/threat-model.md): um download corrompido ou adulterado é descartado.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

type Downloader = Callable[[str, Path], None]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    required: tuple[str, ...]
    """Arquivos que precisam existir após a extração (verificação de integridade básica)."""
    size_mb: int
    sha256: str | None = None
    """Hash do pacote baixado (None só em testes com pacotes gerados na hora)."""


class IntegrityError(RuntimeError):
    """O arquivo baixado não tem o hash esperado."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected: str | None, label: str) -> None:
    if expected is None:
        return
    actual = sha256_of(path)
    if actual != expected.lower():
        path.unlink(missing_ok=True)
        raise IntegrityError(
            f"'{label}' baixado com hash inesperado (esperado {expected[:12]}…, "
            f"obtido {actual[:12]}…); arquivo descartado"
        )


VOSK_SMALL_PT = ModelSpec(
    name="vosk-model-small-pt-0.3",
    url="https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip",
    required=("final.mdl", "HCLr.fst", "Gr.fst", "mfcc.conf"),
    size_mb=31,
    sha256="6e1ce909032e1afa7a88e68a3d628ecafff302bdf195befab308826c395e93b7",
)


def urllib_download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)


def is_installed(spec: ModelSpec, root: Path) -> bool:
    path = root / spec.name
    return all((path / name).exists() for name in spec.required)


def ensure_model(spec: ModelSpec, root: Path, *, download: Downloader = urllib_download) -> Path:
    """Garante o modelo em ``root/spec.name``; baixa e extrai de forma atômica se faltar."""
    target = root / spec.name
    if is_installed(spec, root):
        return target
    root.mkdir(parents=True, exist_ok=True)
    logger.info("Baixando modelo de voz '%s' (~%d MB)", spec.name, spec.size_mb)
    with tempfile.TemporaryDirectory(dir=root, prefix=".download-") as tmp:
        archive = Path(tmp) / "model.zip"
        download(spec.url, archive)
        verify(archive, spec.sha256, spec.name)
        extract_dir = (Path(tmp) / "extracted").resolve()
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                if not (extract_dir / member).resolve().is_relative_to(extract_dir):
                    raise ValueError(f"Arquivo suspeito no pacote do modelo: {member}")
            zf.extractall(extract_dir)
        source = next(
            (c for c in (extract_dir / spec.name, extract_dir) if (c / spec.required[0]).exists()),
            extract_dir,
        )
        missing = [f for f in spec.required if not (source / f).exists()]
        if missing:
            raise RuntimeError(f"Modelo '{spec.name}' incompleto: faltam {', '.join(missing)}")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(source), str(target))
    logger.info("Modelo '%s' pronto em %s", spec.name, target)
    return target


@dataclass(frozen=True)
class FileModelSpec:
    """Modelo distribuído como arquivos soltos (ex.: vozes Piper: .onnx + .onnx.json)."""

    name: str
    files: tuple[tuple[str, str] | tuple[str, str, str], ...]
    """(url, nome do arquivo[, sha256])."""
    size_mb: int


_PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/"

PIPER_PT_BR_FABER = FileModelSpec(
    name="pt_BR-faber-medium",
    files=(
        (
            _PIPER_BASE + "pt_BR-faber-medium.onnx",
            "pt_BR-faber-medium.onnx",
            "75e913eb463db0b6ac7ddf68d3e4c9ac19ace36dc3b710a81039b448352b8ffc",
        ),
        (
            _PIPER_BASE + "pt_BR-faber-medium.onnx.json",
            "pt_BR-faber-medium.onnx.json",
            "fa8a8fecb01ae198bc416381e368d3206f85bad1449a0f11e1f6a9edf85a32f1",
        ),
    ),
    size_mb=63,
)


def ensure_files(
    spec: FileModelSpec, root: Path, *, download: Downloader = urllib_download
) -> Path:
    """Baixa os arquivos que faltam (cada um de forma atômica) e devolve o diretório."""
    root.mkdir(parents=True, exist_ok=True)
    for url, filename, *expected in spec.files:
        target = root / filename
        if target.exists() and target.stat().st_size > 0:
            continue
        logger.info("Baixando '%s' (%s)", filename, spec.name)
        partial = target.with_suffix(target.suffix + ".part")
        download(url, partial)
        verify(partial, expected[0] if expected else None, filename)
        partial.replace(target)
    return root
