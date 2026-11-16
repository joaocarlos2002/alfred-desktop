"""Permissões por categoria e por ferramenta, com confirmação explícita (seção 6).

Ordem de decisão: política da ferramenta → política da categoria → padrão pelo risco
(sensível = perguntar; leitura/ação = permitir). Categorias sensíveis **sempre** pedem confirmação:
uma política "permitir" nelas é tratada como "perguntar".
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Protocol

from alfred.core.action_log import ActionLog
from alfred.core.confirmations import ConfirmationService
from alfred.core.tools import Risk, Tool
from alfred.domain.confirmation import ConfirmationDecision
from alfred.domain.module import Module

logger = logging.getLogger(__name__)


class Policy(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


ALWAYS_CONFIRM = frozenset(
    {"files", "messages", "purchases", "system_settings", "admin", "external_actions"}
)
"""Exclusão/sobrescrita de arquivos, mensagens e publicações, compras, configurações críticas,
instalação/comandos administrativos e ações externas irreversíveis."""

CATEGORY_LABELS = {
    "files": "arquivos",
    "messages": "mensagens e publicações",
    "purchases": "compras e transações",
    "system_settings": "configurações do sistema",
    "admin": "instalação e comandos administrativos",
    "external_actions": "ações externas irreversíveis",
}


class PermissionStore(Protocol):
    async def policies(self) -> dict[tuple[str, str | None], str]: ...

    async def set_policy(self, category: str, tool: str | None, policy: str) -> None: ...


class PermissionService(Module):
    name = "permissions"
    depends_on = ("database", "confirmations")

    def __init__(
        self,
        store: PermissionStore,
        confirmations: ConfirmationService | None,
        action_log: ActionLog,
    ) -> None:
        self._store = store
        self._confirmations = confirmations
        self._log = action_log
        self._policies: dict[tuple[str, str | None], Policy] = {}

    async def start(self) -> None:
        stored = await self._store.policies()
        self._policies = {key: Policy(value) for key, value in stored.items()}

    async def stop(self) -> None:
        return None

    def policy_for(self, tool: Tool) -> Policy:
        policy = self._policies.get((tool.category, tool.name)) or self._policies.get(
            (tool.category, None)
        )
        if policy is None:
            policy = Policy.ASK if tool.risk is Risk.SENSITIVE else Policy.ALLOW
        if tool.category in ALWAYS_CONFIRM and policy is Policy.ALLOW:
            return Policy.ASK
        return policy

    async def set_policy(self, category: str, policy: Policy, tool: str | None = None) -> None:
        await self._store.set_policy(category, tool, policy.value)
        self._policies[(category, tool)] = policy
        logger.info("Permissão %s/%s = %s", category, tool or "*", policy.value)

    async def authorize(self, tool: Tool, summary: str, target: str = "") -> bool:
        """Decide se a ferramenta pode executar agora; pede confirmação quando exigido."""
        policy = self.policy_for(tool)
        params = {"ferramenta": tool.name, "categoria": tool.category, "politica": policy.value}
        async with self._log.record("permission", tool.name, params=params) as rec:
            if policy is Policy.DENY:
                rec.deny("negada pela política")
                return False
            if policy is Policy.ALLOW:
                return True
            if self._confirmations is None:
                rec.deny("confirmação indisponível")
                return False
            decision = await self._confirmations.request(
                action=summary,
                target=target or tool.description,
                impact=_impact(tool),
                category=tool.category,
            )
            rec.result = {"decisao": decision.value}
            if decision is not ConfirmationDecision.APPROVED:
                rec.deny(f"confirmação: {decision.value}")
                return False
            return True


def _impact(tool: Tool) -> str:
    label = CATEGORY_LABELS.get(tool.category)
    if label:
        return f"Ação sensível ({label}). Confira antes de permitir."
    return "Esta ação altera o estado do computador."
