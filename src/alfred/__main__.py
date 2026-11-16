"""Ponto de entrada: ``python -m alfred`` ou ``alfred``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from alfred import __version__
from alfred.app import EXIT_ALREADY_RUNNING, create_application
from alfred.infrastructure.logging_setup import LoggingOptions, configure_logging
from alfred.infrastructure.paths import logs_dir
from alfred.infrastructure.single_instance import SingleInstance

logger = logging.getLogger("alfred")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred", description="ALFRED — Always at your service.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="nível de log (padrão: INFO)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    with SingleInstance() as instance:
        if not instance.acquired:
            print("ALFRED já está em execução.", file=sys.stderr)
            return EXIT_ALREADY_RUNNING

        runtime = configure_logging(LoggingOptions(level=args.log_level, log_dir=logs_dir()))
        logger.info("ALFRED %s iniciando (logs em %s)", __version__, runtime.log_file)
        try:
            return asyncio.run(create_application().run())
        finally:
            runtime.shutdown()


if __name__ == "__main__":
    sys.exit(main())
