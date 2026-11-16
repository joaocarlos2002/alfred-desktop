"""Ponto de entrada: ``python -m alfred`` ou ``alfred``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace

from alfred import __version__
from alfred.app import EXIT_ALREADY_RUNNING, bootstrap, logging_options
from alfred.infrastructure.autostart import Autostart
from alfred.infrastructure.hotkeys import Win32HotkeyListener
from alfred.infrastructure.input_release import Win32InputReleaser
from alfred.infrastructure.logging_setup import configure_logging
from alfred.infrastructure.paths import data_dir, logs_dir
from alfred.infrastructure.single_instance import SingleInstance

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    with SingleInstance() as instance:
        if not instance.acquired:
            print("ALFRED já está em execução.", file=sys.stderr)
            return EXIT_ALREADY_RUNNING

        app, loaded = bootstrap(
            data_dir(),
            autostart=Autostart(),
            hotkeys=Win32HotkeyListener(),
            input_releaser=Win32InputReleaser(),
            with_tray=not args.headless,
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
            from alfred.ui.qt_runtime import run_on_qt

            return run_on_qt(app.run())
        finally:
            runtime.shutdown()


if __name__ == "__main__":
    sys.exit(main())
