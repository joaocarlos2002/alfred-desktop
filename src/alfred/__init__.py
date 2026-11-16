"""ALFRED — Desktop AI Assistant. Always at your service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("alfred-desktop")
except PackageNotFoundError:  # pragma: no cover - executando sem instalação
    __version__ = "0.0.0"

MODULES: tuple[str, ...] = (
    "core",
    "domain",
    "ai",
    "voice",
    "automation",
    "triggers",
    "memory",
    "ui",
    "infrastructure",
    "config",
)
"""Módulos do Modular Monolith (seção 4 da especificação)."""

__all__ = ["MODULES", "__version__"]
