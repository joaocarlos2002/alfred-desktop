"""Eventos publicados quando as configurações mudam."""

from __future__ import annotations

from alfred.config.settings import Settings
from alfred.domain.events import Event


class SettingsChanged(Event):
    section: str
    changed: tuple[str, ...]
    settings: Settings
