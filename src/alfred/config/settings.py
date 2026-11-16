"""Modelos de configuração do ALFRED, com valores padrão documentados.

Cada seção é um modelo imutável. Novas seções entram junto com a funcionalidade que configuram
— nada de configurações de funcionalidades que ainda não existem.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CURRENT_SCHEMA_VERSION = 1

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

MODIFIERS = ("ctrl", "alt", "shift", "win")


def normalize_hotkey(value: str) -> str:
    """Normaliza ``"Ctrl + Alt + Space"`` → ``"ctrl+alt+space"`` e valida o formato."""
    keys = [part.strip().lower() for part in value.split("+") if part.strip()]
    if len(keys) < 2:
        raise ValueError(f"atalho '{value}' precisa de ao menos um modificador e uma tecla")
    *modifiers, key = keys
    if key in MODIFIERS or not key.isalnum():
        raise ValueError(f"atalho '{value}' precisa terminar em uma tecla que não seja modificador")
    unknown = set(modifiers) - set(MODIFIERS)
    if unknown:
        raise ValueError(f"modificador desconhecido: {', '.join(sorted(unknown))}")
    if len(set(modifiers)) != len(modifiers):
        raise ValueError(f"atalho '{value}' repete modificadores")
    return "+".join([*(m for m in MODIFIERS if m in modifiers), key])


class Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", validate_default=True)


class GeneralSettings(Section):
    language: Literal["pt-BR"] = "pt-BR"
    """Idioma da interface e dos comandos."""
    start_with_windows: bool = False
    """Iniciar o ALFRED junto com o Windows (desativado por padrão)."""


class AppearanceSettings(Section):
    theme: Literal["dark"] = "dark"
    widget_visible: bool = True
    widget_position: tuple[int, int] | None = None
    """Posição do widget flutuante (pixels lógicos). ``None``: canto inferior direito."""
    animations: bool = True


class ActivationSettings(Section):
    activate_hotkey: str = "ctrl+alt+space"
    """Atalho global para chamar o ALFRED."""
    emergency_hotkey: str = "ctrl+alt+shift+escape"
    """Atalho global de interrupção de emergência (RF-015)."""

    @field_validator("activate_hotkey", "emergency_hotkey")
    @classmethod
    def _validate_hotkey(cls, value: str) -> str:
        return normalize_hotkey(value)


class LoggingSettings(Section):
    level: LogLevel = "INFO"
    module_levels: dict[str, LogLevel] = Field(default_factory=dict)
    """Níveis por logger, ex.: ``{"alfred.voice": "DEBUG"}``."""
    max_bytes: int = Field(default=5 * 1024 * 1024, ge=64 * 1024)
    backup_count: int = Field(default=5, ge=0, le=50)


class PrivacySettings(Section):
    network_enabled: bool = False
    """Permite acesso à rede (ex.: provedores externos de IA). Local-first: desligado."""
    screen_capture: Literal["ask", "allow", "deny"] = "ask"
    """Política para capturas de tela solicitadas pela IA."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = CURRENT_SCHEMA_VERSION
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    activation: ActivationSettings = Field(default_factory=ActivationSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)

    @classmethod
    def section_names(cls) -> tuple[str, ...]:
        return tuple(name for name in cls.model_fields if name != "schema_version")

    @classmethod
    def section_type(cls, name: str) -> type[Section]:
        if name not in cls.section_names():
            raise KeyError(f"seção de configuração desconhecida: '{name}'")
        annotation = cls.model_fields[name].annotation
        if not (isinstance(annotation, type) and issubclass(annotation, Section)):
            raise TypeError(f"'{name}' não é uma seção")  # pragma: no cover
        return annotation

    def section(self, name: str) -> Section:
        self.section_type(name)
        value: Section = getattr(self, name)
        return value

    def with_section(self, name: str, **changes: Any) -> Settings:
        """Nova instância com ``changes`` aplicadas e validadas na seção ``name``."""
        section_type = self.section_type(name)
        unknown = set(changes) - set(section_type.model_fields)
        if unknown:
            raise KeyError(f"campos desconhecidos em '{name}': {', '.join(sorted(unknown))}")
        updated = section_type.model_validate(self.section(name).model_dump() | changes)
        return self.model_copy(update={name: updated})
