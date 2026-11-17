"""Autodiagnóstico: ``alfred --self-check [--self-check-output arquivo.json]``.

Carrega de verdade cada componente nativo de que o ALFRED depende (áudio, voz, automação, IA,
credenciais) e informa o que está faltando. Serve para validar o executável empacotado (#48) —
um import que só falharia no primeiro uso aparece aqui — e para suporte ao usuário.
Nada é enviado para fora: a única conexão é a consulta ao Ollama local (127.0.0.1).
"""

from __future__ import annotations

import importlib
import json
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from alfred import __version__


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True
    seconds: float = 0.0


def _audio() -> str:
    import sounddevice

    devices = sounddevice.query_devices()
    inputs = sum(1 for d in devices if d["max_input_channels"] > 0)
    return f"PortAudio {sounddevice.get_portaudio_version()[1]}; {inputs} entrada(s)"


def _import(module: str) -> Callable[[], str]:
    def run() -> str:
        loaded = importlib.import_module(module)
        return f"{module} {getattr(loaded, '__version__', '')}".strip()

    return run


def _vosk() -> str:
    import vosk

    vosk.SetLogLevel(-1)
    return "libvosk carregada"


def _whisper() -> str:
    import ctranslate2
    import faster_whisper  # noqa: F401

    return f"ctranslate2 {ctranslate2.__version__}; CUDA: {ctranslate2.get_cuda_device_count()}"


def _piper() -> str:
    import onnxruntime
    import piper  # noqa: F401

    return f"onnxruntime {onnxruntime.__version__}"


def _uia() -> str:
    import pywinauto

    pywinauto.Desktop(backend="uia").windows()
    return f"pywinauto {pywinauto.__version__}"


def _playwright_driver() -> str:
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    missing = [str(p) for p in (Path(node), Path(cli)) if not p.exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    return "driver presente (usa o Microsoft Edge do sistema)"


def _keyring() -> str:
    import keyring

    backend = keyring.get_keyring()
    if "Windows" not in type(backend).__module__:
        raise RuntimeError(f"backend inesperado: {type(backend).__name__}")
    return type(backend).__name__


def _screen() -> str:
    import mss

    with mss.MSS() as sct:
        return f"{len(sct.monitors) - 1} monitor(es)"


def _ollama() -> str:
    import httpx

    response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3)
    models = [m["name"] for m in response.json().get("models", [])]
    if not models:
        raise RuntimeError("Ollama sem modelos (rode: ollama pull qwen2.5:7b)")
    return ", ".join(models)


CHECKS: tuple[tuple[str, Callable[[], str], bool], ...] = (
    ("banco (SQLAlchemy/Alembic)", _import("alembic.command"), True),
    ("interface (Qt)", _import("PySide6.QtWidgets"), True),
    ("áudio (PortAudio)", _audio, True),
    ("palavra de ativação (Vosk)", _vosk, True),
    ("transcrição (faster-whisper)", _whisper, True),
    ("fala (Piper)", _piper, True),
    ("automação (UI Automation)", _uia, True),
    ("navegador (Playwright)", _playwright_driver, True),
    ("captura de tela (mss)", _screen, True),
    ("credenciais (Credential Manager)", _keyring, True),
    ("IA local (Ollama)", _ollama, False),  # opcional: pode estar desinstalado ou parado
)


def run_checks() -> list[Check]:
    results: list[Check] = []
    for name, check, required in CHECKS:
        started = time.perf_counter()
        try:
            detail, ok = check(), True
        except Exception as exc:
            detail, ok = f"{type(exc).__name__}: {exc}", False
        results.append(Check(name, ok, detail, required, round(time.perf_counter() - started, 2)))
    return results


def report(results: list[Check]) -> dict[str, Any]:
    return {
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "windows": platform.version(),
        "ok": all(r.ok for r in results if r.required),
        "checks": [asdict(r) for r in results],
    }


def main(output: Path | None) -> int:
    data = report(run_checks())
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if output is not None:
        output.write_text(text, "utf-8")
    elif sys.stdout is not None:  # executável sem console: use --self-check-output
        print(text)
    return 0 if data["ok"] else 1
