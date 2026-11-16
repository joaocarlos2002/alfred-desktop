"""Download sob demanda de modelos de voz para ``data/models``."""

from __future__ import annotations

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


VOSK_SMALL_PT = ModelSpec(
    name="vosk-model-small-pt-0.3",
    url="https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip",
    required=("final.mdl", "HCLr.fst", "Gr.fst", "mfcc.conf"),
    size_mb=31,
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
    files: tuple[tuple[str, str], ...]
    """(url, nome do arquivo)."""
    size_mb: int


PIPER_PT_BR_FABER = FileModelSpec(
    name="pt_BR-faber-medium",
    files=tuple(
        (
            "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/"
            + filename,
            filename,
        )
        for filename in ("pt_BR-faber-medium.onnx", "pt_BR-faber-medium.onnx.json")
    ),
    size_mb=63,
)


def ensure_files(
    spec: FileModelSpec, root: Path, *, download: Downloader = urllib_download
) -> Path:
    """Baixa os arquivos que faltam (cada um de forma atômica) e devolve o diretório."""
    root.mkdir(parents=True, exist_ok=True)
    for url, filename in spec.files:
        target = root / filename
        if target.exists() and target.stat().st_size > 0:
            continue
        logger.info("Baixando '%s' (%s)", filename, spec.name)
        partial = target.with_suffix(target.suffix + ".part")
        download(url, partial)
        partial.replace(target)
    return root
