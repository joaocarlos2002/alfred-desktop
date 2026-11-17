"""Configurações em tempo de execução: leitura, alteração validada e notificação."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from alfred.config.events import SettingsChanged
from alfred.config.settings import Settings
from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.domain.module import Module

logger = logging.getLogger(__name__)


class SettingsService(Module):
    name = "settings"
    depends_on = ("event_bus",)

    def __init__(self, store: SettingsStore, bus: EventBus, initial: Settings | None = None):
        self._store = store
        self._bus = bus
        self._current = initial
        self._lock = asyncio.Lock()

    @property
    def current(self) -> Settings:
        if self._current is None:
            raise RuntimeError("Configurações ainda não carregadas")
        return self._current

    async def start(self) -> None:
        if self._current is None:
            result = await asyncio.to_thread(self._store.load)
            for warning in result.warnings:
                logger.warning(warning)
            self._current = result.settings

    async def stop(self) -> None:
        return None

    @property
    def bus(self) -> EventBus:
        return self._bus

    async def update(self, section: str, **changes: Any) -> Settings:
        """Aplica, valida, persiste e publica ``SettingsChanged`` se algo mudou.

        Raises:
            KeyError: seção ou campo desconhecido.
            pydantic.ValidationError: valor inválido (nada é alterado).
        """
        async with self._lock:
            old = self.current
            new = old.with_section(section, **changes)
            before, after = old.section(section), new.section(section)
            changed = tuple(k for k in changes if getattr(before, k) != getattr(after, k))
            if not changed:
                return old
            await asyncio.to_thread(self._store.save, new)
            self._current = new

        logger.info("Configuração alterada: %s.%s", section, ",".join(changed))
        await self._bus.publish(SettingsChanged(section=section, changed=changed, settings=new))
        return new
