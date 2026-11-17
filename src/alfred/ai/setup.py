"""Instalação da IA local (#49): Ollama verificado + modelo adequado ao hardware.

Usado pelo instalador (``alfred --setup-ai``) e reutilizável pela interface. Passos:

1. Se o Ollama não estiver instalado, baixa o instalador oficial, **confere a assinatura digital**
   (Authenticode válida, assinante "Ollama") e só então o executa em modo silencioso. Um arquivo
   sem assinatura válida é apagado sem nunca ser executado.
2. Garante o servidor local no ar.
3. Baixa o modelo escolhido pelo hardware (``qwen2.5:7b`` com GPU de 6 GB+ ou 16 GB+ de RAM;
   senão ``qwen2.5:3b``), mostrando o progresso. Se já estiver baixado, não baixa de novo.

Saídas de rede: ``ollama.com`` (instalador) e o registro de modelos do Ollama (feito pelo próprio
servidor local). Nenhum dado do usuário é enviado.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_URL = "http://127.0.0.1:11434"
LARGE_MODEL = "qwen2.5:7b"
SMALL_MODEL = "qwen2.5:3b"

type Progress = Callable[[str, float | None], None]
"""(mensagem, fração 0..1 ou None quando indeterminado)."""


class AISetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class SetupResult:
    model: str
    installed_ollama: bool
    pulled: bool


def choose_model(*, vram_mb: int | None, ram_gb: float) -> str:
    """Modelo de ~5 GB só quando cabe com folga; senão o de ~2 GB (mais leve e rápido)."""
    if (vram_mb is not None and vram_mb >= 6000) or ram_gb >= 16:
        return LARGE_MODEL
    return SMALL_MODEL


def detect_hardware() -> tuple[int | None, float]:
    """(VRAM da NVIDIA em MB ou None, RAM total em GB)."""
    import psutil

    ram_gb = psutil.virtual_memory().total / 2**30
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        vram = int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        vram = None
    return vram, round(ram_gb, 1)


def verify_signature(path: Path) -> bool:
    """Authenticode válida e assinada pela Ollama (checagem do próprio Windows)."""
    if sys.platform != "win32":
        return False
    script = (
        f"$s = Get-AuthenticodeSignature -LiteralPath '{path}'; "
        'Write-Output ("{0}|{1}" -f $s.Status, $s.SignerCertificate.Subject)'
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    status, _, subject = out.stdout.strip().partition("|")
    return status == "Valid" and "ollama" in subject.casefold()


def run_installer(path: Path) -> None:
    """Instalador do Ollama (Inno Setup) em modo silencioso, por usuário."""
    subprocess.run(
        [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
        check=True,
        timeout=900,
    )


async def http_download(url: str, target: Path, progress: Callable[[float], None]) -> None:
    import httpx

    async with (
        httpx.AsyncClient(follow_redirects=True, timeout=60) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        done = 0
        with target.open("wb") as out:
            async for chunk in response.aiter_bytes(1 << 16):
                out.write(chunk)
                done += len(chunk)
                if total:
                    progress(done / total)


class LocalAISetup:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        find: Callable[[], Path | None] | None = None,
        download: Callable[[str, Path, Callable[[float], None]], Awaitable[None]] = http_download,
        verify: Callable[[Path], bool] = verify_signature,
        run_installer: Callable[[Path], None] = run_installer,
        ensure_server: Callable[[], Awaitable[bool]] | None = None,
        hardware: Callable[[], tuple[int | None, float]] = detect_hardware,
        workdir: Path | None = None,
        model: str | None = None,
    ) -> None:
        from alfred.ai.ollama import OllamaProvider, find_executable

        self._transport = transport
        self._find = find or find_executable
        self._download = download
        self._verify = verify
        self._run_installer = run_installer
        self._provider = OllamaProvider(OLLAMA_URL, transport=transport)
        self._ensure_server = ensure_server or (lambda: self._provider.ensure_running(timeout=90))
        self._hardware = hardware
        self._workdir = workdir
        self._model = model

    async def run(self, progress: Progress) -> SetupResult:
        installed = await self._install_ollama(progress)
        progress("Iniciando o servidor de IA local…", None)
        if not await self._ensure_server():
            raise AISetupError("O Ollama foi instalado, mas o servidor não respondeu.")
        vram, ram = self._hardware()
        model = self._model or choose_model(vram_mb=vram, ram_gb=ram)
        if model in await self._provider.list_models():
            progress(f"Modelo {model} já está pronto.", 1.0)
            return SetupResult(model, installed, pulled=False)
        await self._pull(model, progress)
        progress(f"Pronto: {model} instalado.", 1.0)
        return SetupResult(model, installed, pulled=True)

    async def _install_ollama(self, progress: Progress) -> bool:
        if self._find() is not None:
            progress("Ollama já está instalado.", None)
            return False
        folder = self._workdir or Path(tempfile.mkdtemp(prefix="alfred-ollama-"))
        target = folder / "OllamaSetup.exe"
        progress("Baixando o Ollama…", 0.0)
        await self._download(
            OLLAMA_INSTALLER_URL, target, lambda f: progress("Baixando o Ollama…", f)
        )
        progress("Conferindo a assinatura digital do instalador…", None)
        if not self._verify(target):
            target.unlink(missing_ok=True)
            raise AISetupError(
                "O instalador baixado não tem assinatura válida da Ollama; descartado."
            )
        progress("Instalando o Ollama…", None)
        await asyncio.to_thread(self._run_installer, target)
        target.unlink(missing_ok=True)
        if self._find() is None:
            raise AISetupError("A instalação do Ollama não foi concluída.")
        return True

    async def _pull(self, model: str, progress: Progress) -> None:
        import httpx

        progress(f"Baixando o modelo {model}…", 0.0)
        async with (
            httpx.AsyncClient(
                base_url=OLLAMA_URL, timeout=None, transport=self._transport
            ) as client,
            client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise AISetupError(f"Falha ao baixar o modelo: {data['error']}")
                total, done = data.get("total"), data.get("completed")
                if total and done is not None:
                    progress(f"Baixando o modelo {model}…", round(done / total, 4))
