"""Credenciais no Windows Credential Manager (seção 6) — nunca em arquivo, banco ou log."""

from __future__ import annotations

import logging

from alfred.infrastructure.logging_setup import SecretRedactor, redactor

logger = logging.getLogger(__name__)

SERVICE = "ALFRED"


class CredentialStore:
    def __init__(self, service: str = SERVICE, secret_redactor: SecretRedactor = redactor) -> None:
        self._service = service
        self._redactor = secret_redactor

    def get(self, name: str) -> str | None:
        import keyring

        secret = keyring.get_password(self._service, name)
        if secret:
            self._redactor.register(secret)  # se aparecer em algum log, é mascarado
        return secret

    def set(self, name: str, secret: str) -> None:
        import keyring

        keyring.set_password(self._service, name, secret)
        self._redactor.register(secret)
        logger.info("Credencial de '%s' salva no Credential Manager", name)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self._service, name)
        except PasswordDeleteError:
            return
        logger.info("Credencial de '%s' removida do Credential Manager", name)
