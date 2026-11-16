"""Ponto de entrada: ``python -m alfred`` ou ``alfred``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from alfred import __version__
from alfred.app import EXIT_ALREADY_RUNNING, bootstrap, logging_options
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    with SingleInstance() as instance:
        if not instance.acquired:
            print("ALFRED já está em execução.", file=sys.stderr)
            return EXIT_ALREADY_RUNNING

        app, loaded = bootstrap(data_dir())
        runtime = configure_logging(
            logging_options(loaded.settings.logging, logs_dir(), level_override=args.log_level)
        )
        logger.info("ALFRED %s iniciando (dados em %s)", __version__, data_dir())
        for warning in loaded.warnings:
            logger.warning(warning)
        try:
            return asyncio.run(app.run())
        finally:
            runtime.shutdown()


if __name__ == "__main__":
    sys.exit(main())
