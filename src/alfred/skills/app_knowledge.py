"""Skill "aprender sobre os programas" (#125).

O ALFRED explora, sob pedido, a interface de um programa via UI Automation (somente leitura: lê a
árvore de elementos, não clica em nada) e guarda um mapa local: elementos com nome, tipo e id, e
atalhos de teclado que aparecem nos rótulos. O usuário também pode ensinar procedimentos ("para
exportar PDF no Word: Arquivo > Exportar..."). O planejador vê quais programas conhece e consulta
o mapa antes de agir, com menos tentativa e erro.

Segurança: o que a tela de um programa diz é conteúdo de terceiros (``untrusted_output``), nunca
instrução; explorar um programa pela primeira vez pede permissão; gravar um procedimento é uma
ação (exige confirmação se a tarefa já leu conteúdo externo — threat model T1).
"""

from __future__ import annotations

import builtins
import re
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, Field

from alfred.core.tools import Risk, Tool
from alfred.infrastructure.db.app_knowledge_repository import Knowledge
from alfred.skills.base import Skill

MAX_ELEMENTS = 200
SHORTCUT = re.compile(
    r"\b((?:(?:Ctrl|Alt|Shift|Win)\+)+(?:[A-Z0-9]|F\d{1,2}|Enter|Tab|Del|Esc)|F\d{1,2})\b",
    re.IGNORECASE,
)


def app_key(process: str) -> str:
    name = process.casefold().strip()
    return name if name.endswith(".exe") else f"{name}.exe"


def extract_shortcuts(labels: list[str]) -> list[str]:
    found: list[str] = []
    for label in labels:
        for match in SHORTCUT.findall(label):
            shortcut = "+".join(
                part.capitalize() if len(part) > 1 else part.upper() for part in match.split("+")
            )
            shortcut = re.sub(r"\bf(\d+)\b", r"F\1", shortcut, flags=re.IGNORECASE)
            if shortcut not in found:
                found.append(shortcut)
    return found


class Store(Protocol):
    async def list(self) -> builtins.list[Knowledge]: ...

    async def save_exploration(
        self,
        app_key: str,
        title: str | None,
        elements: builtins.list[dict[str, Any]],
        shortcuts: builtins.list[str],
    ) -> Knowledge: ...

    async def add_procedure(
        self, app_key: str, title: str | None, name: str, steps: str
    ) -> Knowledge: ...

    async def delete_procedure(self, app_key: str, name: str) -> bool: ...

    async def delete(self, app_key: str) -> bool: ...


class Windows(Protocol):
    async def resolve(self, query: str) -> Any: ...


class Uia(Protocol):
    async def describe(self, window: str, limit: int = 200) -> list[dict[str, Any]]: ...


class LearnParams(BaseModel):
    janela: str = Field(min_length=1, description="Janela do programa (título ou processo)")


class ConsultParams(BaseModel):
    programa: str = Field(min_length=1, description="Nome do programa ou do executável")


class TeachParams(BaseModel):
    programa: str = Field(min_length=1)
    nome: str = Field(min_length=1, description="Nome do procedimento (ex.: exportar PDF)")
    passos: str = Field(min_length=1, description="Passos, como o usuário explicou")


class AppKnowledgeSkill(Skill):
    key: ClassVar[str] = "programas"
    name: ClassVar[str] = "Aprender os programas"
    description: ClassVar[str] = "Explora e lembra como seus programas funcionam"

    def __init__(self, store: Store, windows: Windows, uia: Uia) -> None:
        self._store = store
        self._windows = windows
        self._uia = uia
        self._known: dict[str, Knowledge] = {}

    async def activate(self) -> None:
        self._known = {k.app_key: k for k in await self._store.list()}

    # ------------------------------------------------------------------ consulta
    def _match(self, query: str) -> Knowledge | None:
        wanted = query.casefold().strip()
        for knowledge in self._known.values():
            names = (
                knowledge.app_key,
                knowledge.app_key.removesuffix(".exe"),
                knowledge.title or "",
            )
            if any(wanted == n.casefold() or (wanted and wanted in n.casefold()) for n in names):
                return knowledge
        return None

    async def programs(self) -> list[Knowledge]:
        self._known = {k.app_key: k for k in await self._store.list()}
        return sorted(self._known.values(), key=lambda k: k.app_key)

    async def forget(self, key: str) -> None:
        await self._store.delete(key)
        self._known.pop(key, None)

    async def forget_procedure(self, key: str, name: str) -> None:
        await self._store.delete_procedure(key, name)
        await self.programs()

    async def context(self) -> str:
        if not self._known:
            self._known = {k.app_key: k for k in await self._store.list()}
        if not self._known:
            return ""
        lines = []
        for k in sorted(self._known.values(), key=lambda k: k.app_key):
            label = f"{_display(k)} ({k.app_key})"
            if k.procedures:
                label += " — procedimentos: " + ", ".join(sorted(k.procedures))
            lines.append(f"- {label}")
        return (
            "Programas que você já aprendeu (use consultar_programa antes de operá-los):\n"
            + "\n".join(lines)
        )

    # ------------------------------------------------------------------ ferramentas
    def tools(self) -> list[Tool]:
        def first_time(params: LearnParams) -> str | None:
            if self._match(params.janela) is not None:
                return None
            return (
                f"Explorar a interface de '{params.janela}' em somente leitura (lê os nomes dos "
                "botões e menus, não clica em nada) e guardar o mapa neste computador."
            )

        async def learn(p: LearnParams) -> dict[str, Any]:
            window = await self._windows.resolve(p.janela)
            key = app_key(window.process)
            raw = await self._uia.describe(p.janela, limit=MAX_ELEMENTS * 2)
            elements: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str]] = set()
            for item in raw:
                name = str(item.get("nome") or "").strip()
                kind = str(item.get("tipo") or "")
                ident = str(item.get("id") or "")
                if not name or (name, kind, ident) in seen:
                    continue
                seen.add((name, kind, ident))
                elements.append({"nome": name, "tipo": kind, "id": ident})
                if len(elements) >= MAX_ELEMENTS:
                    break
            shortcuts = extract_shortcuts([e["nome"] for e in elements])
            saved = await self._store.save_exploration(key, window.title, elements, shortcuts)
            self._known[key] = saved
            return {
                "programa": key,
                "titulo": window.title,
                "elementos": len(elements),
                "atalhos": shortcuts,
            }

        async def consult(p: ConsultParams) -> dict[str, Any]:
            knowledge = self._match(p.programa)
            if knowledge is None:
                return {"conhecido": False, "dica": "Use aprender_programa com a janela aberta."}
            return {
                "conhecido": True,
                "programa": knowledge.app_key,
                "titulo": knowledge.title,
                "elementos": knowledge.elements,
                "atalhos": knowledge.shortcuts,
                "procedimentos": knowledge.procedures,
            }

        async def teach(p: TeachParams) -> dict[str, Any]:
            known = self._match(p.programa)
            title = known.title if known else None
            if known is not None:
                key = known.app_key
            else:
                try:  # programa aberto: identifica o executável real (Word -> winword.exe)
                    window = await self._windows.resolve(p.programa)
                    key, title = app_key(window.process), window.title
                except Exception:
                    key = app_key(p.programa)
            saved = await self._store.add_procedure(key, title, p.nome, p.passos)
            self._known[key] = saved
            return {"programa": key, "procedimento": p.nome}

        return [
            Tool(
                "aprender_programa",
                "Explora a interface de um programa aberto (somente leitura) e guarda o mapa.",
                LearnParams,
                learn,
                "skills",
                Risk.READ,
                timeout=60,
                untrusted_output=True,
                needs_confirmation=first_time,
            ),
            Tool(
                "consultar_programa",
                "Mostra o que o ALFRED sabe sobre um programa (elementos, atalhos, procedimentos).",
                ConsultParams,
                consult,
                "skills",
                Risk.READ,
                idempotent=True,
                untrusted_output=True,
            ),
            Tool(
                "ensinar_procedimento",
                "Guarda um procedimento que o usuário ensinou para um programa.",
                TeachParams,
                teach,
                "skills",
            ),
        ]


def _display(knowledge: Knowledge) -> str:
    title: str = knowledge.title or knowledge.app_key
    return title.rsplit(" - ", 1)[-1] if " - " in title else title
