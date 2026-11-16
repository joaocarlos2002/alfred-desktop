"""Descoberta, resolução por nome e abertura de aplicações (RF-006).

A fonte é a mesma do Menu Iniciar (``Get-StartApps``): cobre apps clássicos e UWP, e todos abrem
por ``shell:AppsFolder\\<AppID>``, exatamente como o Menu Iniciar faz.
"""

from __future__ import annotations

import difflib
import json
import logging
import subprocess
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartApp:
    name: str
    app_id: str


class AppNotFoundError(LookupError):
    pass


class AmbiguousAppError(LookupError):
    def __init__(self, query: str, candidates: list[StartApp]) -> None:
        names = ", ".join(c.name for c in candidates)
        super().__init__(f"'{query}' pode ser: {names}")
        self.candidates = candidates


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def powershell_start_apps() -> list[StartApp]:
    # Sem isso o PowerShell escreve na página de código do console (cp850/1252) e nomes com
    # acentos ("Configurações") quebram a decodificação.
    command = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=True,
    )
    data = json.loads(completed.stdout or "[]")
    items = data if isinstance(data, list) else [data]
    return [StartApp(str(i["Name"]), str(i["AppID"])) for i in items if i.get("AppID")]


def launch_start_app(app: StartApp) -> None:
    """O explorer.exe repassa a abertura ao shell e termina logo; aguardamos para não vazar."""
    subprocess.run(["explorer.exe", f"shell:AppsFolder\\{app.app_id}"], check=False, timeout=30)


class AppCatalog:
    AMBIGUITY_MARGIN = 0.08
    MIN_SCORE = 0.6

    def __init__(
        self,
        lister: Callable[[], list[StartApp]] = powershell_start_apps,
        aliases: Mapping[str, list[str]] | None = None,
    ) -> None:
        self._lister = lister
        self.aliases = {k: list(v) for k, v in (aliases or {}).items()}
        self._apps: list[StartApp] | None = None

    @property
    def apps(self) -> list[StartApp]:
        if self._apps is None:
            self.refresh()
        return self._apps or []

    def refresh(self) -> None:
        seen: set[str] = set()
        unique: list[StartApp] = []
        for app in self._lister():
            key = normalize(app.name)
            if key not in seen:
                seen.add(key)
                unique.append(app)
        self._apps = unique
        logger.info("%d aplicações encontradas no Menu Iniciar", len(unique))

    def _score(self, query: str, app: StartApp) -> float:
        best = 0.0
        for name in (app.name, *self.aliases.get(app.name, [])):
            candidate = normalize(name)
            if candidate == query:
                return 1.0
            if query in candidate.split() or (len(query) >= 4 and candidate.startswith(query)):
                best = max(best, 0.9)
            best = max(best, difflib.SequenceMatcher(None, query, candidate).ratio())
        return best

    def candidates(self, query: str, limit: int = 5) -> list[tuple[StartApp, float]]:
        wanted = normalize(query)
        scored = [(app, self._score(wanted, app)) for app in self.apps]
        scored = [item for item in scored if item[1] >= self.MIN_SCORE]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def resolve(self, query: str) -> StartApp:
        found = self.candidates(query)
        if not found:
            raise AppNotFoundError(f"Nenhuma aplicação parecida com '{query}'")
        best, score = found[0]
        if score < 1:
            rivals = [app for app, s in found[1:] if score - s < self.AMBIGUITY_MARGIN]
            if rivals:
                raise AmbiguousAppError(query, [best, *rivals])
        return best
