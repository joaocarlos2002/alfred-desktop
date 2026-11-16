"""Mede tempo de inicialização, RAM e CPU do ALFRED em idle.

Uso:
    uv run python scripts/measure_idle.py --seconds 60

Inicia o ALFRED com um diretório de dados temporário, espera o log "ALFRED em execução",
amostra o processo durante ``--seconds`` e imprime o resultado em JSON. Metas iniciais
(seção 9): inicialização ≤ 5 s, RAM em idle ≤ 150 MB, CPU média em idle ≤ 2%.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

READY_MARKER = "ALFRED em execu"


def wait_ready(log_file: Path, timeout: float) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if log_file.exists() and READY_MARKER in log_file.read_text("utf-8", errors="replace"):
            return time.perf_counter() - started
        time.sleep(0.05)
    raise TimeoutError(f"ALFRED não ficou pronto em {timeout} s")


def user_models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "ALFRED" if base else Path.home() / ".alfred") / "models"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--voice",
        action="store_true",
        help="liga microfone e palavra de ativação (usa os modelos já baixados do usuário)",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="alfred-bench-", ignore_cleanup_errors=True) as tmp:
        env = {**os.environ, "ALFRED_DATA_DIR": tmp}
        if args.voice:
            # Reaproveita os modelos já baixados (nada é baixado de novo) e liga a voz.
            env["ALFRED_MODELS_DIR"] = str(user_models_dir())
            settings = {"voice": {"enabled": True, "wake_word_enabled": True}}
            (Path(tmp) / "settings.json").write_text(json.dumps(settings), "utf-8")
        command = [sys.executable, "-m", "alfred"] + (["--headless"] if args.headless else [])
        launched = time.perf_counter()
        process = subprocess.Popen(command, env=env)
        launcher = psutil.Process(process.pid)
        try:
            wait_ready(Path(tmp) / "logs" / "alfred.log", timeout=30)
            startup = time.perf_counter() - launched
            # O python.exe de um venv no Windows é um launcher que cria o interpretador real
            # como processo filho: mede-se o filho, não o launcher.
            # Filhos transitórios (ex.: powershell do catálogo de apps) são ignorados.
            pythons = [
                c
                for c in launcher.children(recursive=True)
                if c.name().lower().startswith("python")
            ]
            proc = pythons[-1] if pythons else launcher
            proc.cpu_percent(None)
            cpu: list[float] = []
            rss: list[int] = []
            deadline = time.perf_counter() + args.seconds
            while time.perf_counter() < deadline:
                time.sleep(args.interval)
                cpu.append(proc.cpu_percent(None) / (psutil.cpu_count() or 1))
                rss.append(proc.memory_info().rss)
        finally:
            tree = [*launcher.children(recursive=True), launcher]
            for member in tree:
                member.terminate()
            psutil.wait_procs(tree, timeout=10)

    result = {
        "mode": ("headless" if args.headless else "qt+tray") + ("+voz" if args.voice else ""),
        "startup_seconds": round(startup, 2),
        "idle_seconds": args.seconds,
        "rss_mb_avg": round(sum(rss) / len(rss) / 2**20, 1),
        "rss_mb_max": round(max(rss) / 2**20, 1),
        "cpu_percent_avg": round(sum(cpu) / len(cpu), 2),
        "cpu_percent_max": round(max(cpu), 2),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
