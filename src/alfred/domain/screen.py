"""Geometria de tela e contrato de anotação visual (RF-007)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScreenRect(BaseModel):
    """Retângulo em **pixels físicos** do desktop virtual (como reportado pelo Win32/UIA)."""

    model_config = ConfigDict(frozen=True)

    x: int
    y: int
    width: int = Field(ge=0)
    height: int = Field(ge=0)

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


class ScreenAnnotator(Protocol):
    """Desenha indicações visuais sobre a tela sem capturar mouse nem teclado."""

    def highlight(
        self, rect: ScreenRect, *, label: str | None = None, duration: float = 1.5
    ) -> UUID: ...

    def clear(self, highlight_id: UUID) -> None: ...

    def clear_all(self) -> None: ...


class VirtualCursor(Protocol):
    """Cursor exclusivamente visual (RF-007): mostra o que o ALFRED está fazendo.

    Nunca move o cursor físico nem simula um dispositivo de mouse. Coordenadas em pixels físicos.
    """

    def move_to(self, x: int, y: int) -> None: ...

    def click(self) -> None:
        """Indica visualmente um clique na posição atual."""
        ...

    def point_at(self, rect: ScreenRect, *, label: str | None = None) -> None:
        """Move até o centro do elemento e o destaca."""
        ...

    def hide(self) -> None: ...
