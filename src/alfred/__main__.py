"""Ponto de entrada: ``python -m alfred`` ou ``alfred``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from alfred import __version__
from alfred.app import EXIT_ALREADY_RUNNING, create_application
from alfred.infrastructure.single_instance import SingleInstance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred", description="ALFRED — Always at your service.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    with SingleInstance() as instance:
        if not instance.acquired:
            print("ALFRED já está em execução.", file=sys.stderr)
            return EXIT_ALREADY_RUNNING
        return asyncio.run(create_application().run())


if __name__ == "__main__":
    sys.exit(main())
