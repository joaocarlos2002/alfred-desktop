"""Contrato de módulo gerenciado pelo ciclo de vida da aplicação."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class Health(BaseModel):
    """Estado de saúde reportado por um módulo."""

    model_config = ConfigDict(frozen=True)

    status: HealthStatus
    detail: str | None = None


class Module(ABC):
    """Unidade com ciclo de vida própria dentro do Modular Monolith.

    Subclasses definem ``name`` (único) e, opcionalmente, ``depends_on`` com os nomes dos módulos
    que precisam estar em execução antes delas. Um módulo ``optional`` que falhe ao iniciar não
    derruba a aplicação: ele e seus dependentes opcionais são ignorados e a falha é registrada.
    """

    name: str
    depends_on: tuple[str, ...] = ()
    optional: bool = False

    @abstractmethod
    async def start(self) -> None:
        """Aloca recursos e começa a operar."""

    @abstractmethod
    async def stop(self) -> None:
        """Libera os recursos. Chamado em ordem inversa à de inicialização."""

    async def health(self) -> Health:
        return Health(status=HealthStatus.HEALTHY)
