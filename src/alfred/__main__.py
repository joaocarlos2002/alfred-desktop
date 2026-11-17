"""Ponto de entrada: ``python -m alfred`` ou ``alfred``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from alfred import __version__
from alfred.ai.ollama import OllamaProvider
from alfred.app import EXIT_ALREADY_RUNNING, bootstrap, logging_options
from alfred.automation.apps import powershell_start_apps
from alfred.infrastructure.autostart import Autostart
from alfred.infrastructure.credentials import CredentialStore
from alfred.infrastructure.hotkeys import Win32HotkeyListener
from alfred.infrastructure.input_release import Win32InputReleaser
from alfred.infrastructure.logging_setup import configure_logging
from alfred.infrastructure.paths import DATA_DIR_ENV, data_dir, logs_dir, models_dir
from alfred.infrastructure.single_instance import SingleInstance, instance_name
from alfred.voice.devices import SoundDeviceBackend

logger = logging.getLogger("alfred")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred", description="ALFRED — Always at your service.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="sobrescreve o nível de log das configurações",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="executa sem interface (sem Qt nem bandeja), útil para diagnóstico",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="verifica os componentes nativos (voz, automação, IA) e sai",
    )
    parser.add_argument(
        "--self-check-output",
        type=Path,
        default=None,
        help="grava o resultado do --self-check neste arquivo JSON",
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="SECAO.CAMPO=VALOR",
        default=[],
        help="altera uma configuração (validada) e sai; ex.: general.start_with_windows=true",
    )
    parser.add_argument(
        "--setup-ai",
        action="store_true",
        help="instala a IA local (Ollama verificado + modelo) com uma janela de progresso",
    )
    return parser


def apply_settings(assignments: Sequence[str]) -> int:
    """``--set``: valida tudo antes de gravar; nada muda se qualquer item for inválido."""
    import json

    from pydantic import ValidationError

    from alfred.config.store import SETTINGS_FILE, SettingsStore

    store = SettingsStore(data_dir() / SETTINGS_FILE)
    settings = store.load().settings
    try:
        for assignment in assignments:
            key, sep, raw = assignment.partition("=")
            section, dot, field = key.strip().partition(".")
            if not sep or not dot:
                raise ValueError(f"use SECAO.CAMPO=VALOR (recebido: {assignment!r})")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = raw  # texto simples
            settings = settings.with_section(section, **{field: value})
    except (KeyError, ValueError, ValidationError) as exc:
        if sys.stderr is not None:
            print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2
    store.save(settings)
    return 0


def setup_ai() -> int:
    """``--setup-ai``: instala Ollama + modelo com janela de progresso (usado pelo instalador)."""
    from alfred.ui.ai_setup_window import run_setup_window

    return run_setup_window()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_check:
        from alfred.diagnostics import main as self_check

        return self_check(args.self_check_output)
    if args.set:
        return apply_settings(args.set)
    if args.setup_ai:
        return setup_ai()

    custom = data_dir() if os.environ.get(DATA_DIR_ENV) else None
    with SingleInstance(instance_name(custom)) as instance:
        if not instance.acquired:
            print("ALFRED já está em execução.", file=sys.stderr)
            return EXIT_ALREADY_RUNNING

        app, loaded = bootstrap(
            data_dir(),
            autostart=Autostart(),
            hotkeys=Win32HotkeyListener(),
            input_releaser=Win32InputReleaser(),
            audio_backend=SoundDeviceBackend(),
            app_lister=powershell_start_apps,
            ai_providers=(OllamaProvider(),),
            credentials=CredentialStore(),
            with_ui=not args.headless,
            system_observers=True,
            models_root=models_dir(),
        )
        options = logging_options(
            loaded.settings.logging, logs_dir(), level_override=args.log_level
        )
        # Sob pythonw.exe (inicialização automática) não há console: sys.stderr é None.
        runtime = configure_logging(replace(options, console=sys.stderr is not None))
        logger.info("ALFRED %s iniciando (dados em %s)", __version__, data_dir())
        for warning in loaded.warnings:
            logger.warning(warning)
        try:
            if args.headless:
                return asyncio.run(app.run())
            from alfred.ui.qt_runtime import run_on_qt, shutdown_qt

            try:
                return run_on_qt(app.run())
            finally:
                shutdown_qt()
        finally:
            runtime.shutdown()


if __name__ == "__main__":
    sys.exit(main())
