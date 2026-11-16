"""Diretórios de dados locais do ALFRED."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_ENV = "ALFRED_DATA_DIR"
MODELS_DIR_ENV = "ALFRED_MODELS_DIR"


def data_dir() -> Path:
    """Diretório raiz de dados.

    Ordem: variável ``ALFRED_DATA_DIR`` → ``%LOCALAPPDATA%\\ALFRED`` → ``~/.alfred``.
    Em desenvolvimento, use ``ALFRED_DATA_DIR=data`` para gravar em ``./data``.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ALFRED"
    return Path.home() / ".alfred"


def logs_dir() -> Path:
    return data_dir() / "logs"


def cache_dir() -> Path:
    return data_dir() / "cache"


def models_dir() -> Path:
    """Modelos de voz; ``ALFRED_MODELS_DIR`` reaproveita os já baixados (ex.: benchmarks)."""
    override = os.environ.get(MODELS_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return data_dir() / "models"


def exports_dir() -> Path:
    return data_dir() / "exports"
