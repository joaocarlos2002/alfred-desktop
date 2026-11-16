"""Classificação de falhas de etapas e política de recuperação (Fase 6, Seção 9).

- **transitória**: pode passar sozinha (tempo esgotado, usuário usando o teclado, rede). Só é
  repetida automaticamente quando é seguro: a ferramenta é idempotente ou a falha aconteceu antes
  de qualquer efeito (``nothing_done``). Uma digitação que estourou o tempo no meio, por exemplo,
  nunca é repetida às cegas.
- **de ambiente**: o computador impediu a ação (janela não aceitou foco, erro do sistema).
- **de plano**: o plano da IA estava errado (argumentos inválidos, elemento inexistente) — o erro
  volta ao modelo para replanejar, até um limite de falhas por tarefa.
- **negada**: o usuário recusou; o modelo não deve tentar contornar.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorKind(StrEnum):
    TRANSIENT = "transitorio"
    ENVIRONMENT = "ambiente"
    PLAN = "plano"
    DENIED = "negado"


GUIDANCE: dict[ErrorKind, str] = {
    ErrorKind.TRANSIENT: "Falha temporária que persistiu após novas tentativas.",
    ErrorKind.ENVIRONMENT: (
        "O computador impediu a ação: tente outro caminho ou explique ao usuário."
    ),
    ErrorKind.PLAN: "O plano não funcionou: corrija os argumentos ou tente outra abordagem.",
    ErrorKind.DENIED: "O usuário não autorizou: não tente contornar, apenas explique.",
}


@dataclass(frozen=True)
class Classified:
    kind: ErrorKind
    retry_safe: bool


def _chain(exc: BaseException) -> list[BaseException]:
    found: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in found:
        found.append(current)
        current = current.__cause__ or current.__context__
    return found


def classify(exc: BaseException, *, idempotent: bool = False) -> Classified:
    chain = _chain(exc)
    nothing_done = any(getattr(e, "nothing_done", False) for e in chain)
    if any(isinstance(e, PermissionError) for e in chain):
        return Classified(ErrorKind.DENIED, retry_safe=False)
    if any(
        getattr(e, "transient", False) or isinstance(e, TimeoutError | ConnectionError)
        for e in chain
    ):
        return Classified(ErrorKind.TRANSIENT, retry_safe=idempotent or nothing_done)
    if isinstance(exc, LookupError | ValueError | TypeError | KeyError):
        return Classified(ErrorKind.PLAN, retry_safe=False)
    return Classified(ErrorKind.ENVIRONMENT, retry_safe=False)
