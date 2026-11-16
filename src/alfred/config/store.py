"""Persistência das configurações em JSON, com migração e recuperação de falhas."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from alfred.config.settings import CURRENT_SCHEMA_VERSION, Section, Settings

SETTINGS_FILE = "settings.json"

type Migration = Callable[[dict[str, Any]], dict[str, Any]]

MIGRATIONS: Mapping[int, Migration] = {}
"""Migrações do schema: ``{versão_origem: função}`` leva de N para N + 1."""


@dataclass(frozen=True)
class LoadResult:
    settings: Settings
    warnings: tuple[str, ...] = ()


class SettingsStore:
    def __init__(self, path: Path, *, migrations: Mapping[int, Migration] = MIGRATIONS) -> None:
        self.path = path
        self._migrations = migrations

    def load(self) -> LoadResult:
        """Carrega as configurações. Nunca falha: problemas viram avisos e valores padrão."""
        if not self.path.exists():
            settings = Settings()
            self.save(settings)
            return LoadResult(settings)

        warnings: list[str] = []
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("o conteúdo não é um objeto JSON")
        except (OSError, ValueError) as exc:
            backup = self._backup("corrupt")
            warnings.append(
                f"Configurações ilegíveis ({exc}); valores padrão restaurados. "
                f"Backup: {backup.name}"
            )
            settings = Settings()
            self.save(settings)
            return LoadResult(settings, tuple(warnings))

        dirty = False
        version = raw.get("schema_version", CURRENT_SCHEMA_VERSION)
        if not isinstance(version, int):
            warnings.append(f"schema_version inválido ({version!r}); assumindo a versão atual")
            version = CURRENT_SCHEMA_VERSION

        newer = version > CURRENT_SCHEMA_VERSION
        if newer:
            warnings.append(
                f"Configurações da versão {version}, mais nova que a suportada "
                f"({CURRENT_SCHEMA_VERSION}); lendo apenas campos compatíveis sem sobrescrever"
            )
        elif version < CURRENT_SCHEMA_VERSION:
            self._backup(f"v{version}")
            while version < CURRENT_SCHEMA_VERSION:
                migration = self._migrations.get(version)
                if migration is None:
                    warnings.append(
                        f"Sem migração da versão {version}; campos incompatíveis serão ignorados"
                    )
                    break
                raw = migration(dict(raw))
                version += 1
            dirty = True

        sections: dict[str, Section] = {}
        for name in Settings.section_names():
            section, problems = _load_section(name, raw.get(name, {}))
            sections[name] = section
            if problems:
                dirty = True
                warnings.extend(problems)

        unknown = set(raw) - {*Settings.section_names(), "schema_version"}
        if unknown:
            warnings.append(f"Seções desconhecidas ignoradas: {', '.join(sorted(unknown))}")

        settings = Settings.model_validate(sections)
        if dirty and not newer:
            self.save(settings)
        return LoadResult(settings, tuple(warnings))

    def save(self, settings: Settings) -> None:
        """Grava de forma atômica (arquivo temporário + rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _backup(self, label: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.{label}-{stamp}.bak")
        shutil.copy2(self.path, target)
        return target


def _load_section(name: str, value: Any) -> tuple[Section, list[str]]:
    """Valida uma seção; campos inválidos voltam ao padrão sem descartar os demais."""
    section_type = Settings.section_type(name)
    if not isinstance(value, dict):
        return section_type(), [f"Seção '{name}' inválida; valores padrão restaurados"]
    try:
        return section_type.model_validate(value), []
    except ValidationError as exc:
        bad_fields = {str(error["loc"][0]) for error in exc.errors() if error["loc"]}
        kept = {k: v for k, v in value.items() if k not in bad_fields}
        problems = [
            f"Configuração '{name}.{field}' inválida; valor padrão restaurado"
            for field in sorted(bad_fields)
        ]
        try:
            return section_type.model_validate(kept), problems
        except ValidationError:
            return section_type(), [f"Seção '{name}' inválida; valores padrão restaurados"]
