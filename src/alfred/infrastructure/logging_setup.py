"""Logging estruturado (JSON Lines), assíncrono, rotativo e com redação de segredos.

Os registros são enfileirados pela thread que loga (``QueueHandler``) e gravados por uma thread
dedicada (``QueueListener``), para que o I/O de disco nunca bloqueie o event loop. O
``correlation_id`` é capturado na thread de origem, no momento do log.
"""

from __future__ import annotations

import copy
import json
import logging
import queue
import re
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import Any

from alfred.domain.events import current_correlation_id

LOG_FILE_NAME = "alfred.log"
REDACTED = "***"

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "correlation_id"}
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer antes de chave/valor: em "Authorization: Bearer <tok>" o valor seria "Bearer".
    re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9._~+/=-]+)"),
    # chave: valor  /  chave=valor  (password, senha, token, api_key, secret...)
    re.compile(
        r"(?i)\b(pass(?:word|wd)?|senha|secret|token|api[_-]?key|access[_-]?key|authorization)"
        r"(\\?[\"']?\s*[:=]\s*\\?[\"']?)([^\s\"',;\\]+)"
    ),
    # formatos conhecidos de chaves de API
    re.compile(r"()()\b(sk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"()()\b(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"()()\b(AKIA[0-9A-Z]{16})"),
    re.compile(r"()()\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"),
)


class SecretRedactor:
    """Mascara segredos conhecidos (padrões) e valores registrados explicitamente."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: str) -> None:
        """Registra um valor sensível (ex.: chave lida do Credential Manager)."""
        if len(secret) >= 4:
            with self._lock:
                self._values.add(secret)

    def redact(self, text: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            text = text.replace(value, REDACTED)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        return text


redactor = SecretRedactor()
"""Redator global usado por todos os handlers do ALFRED."""


class JsonFormatter(logging.Formatter):
    def __init__(self, secret_redactor: SecretRedactor) -> None:
        super().__init__()
        self._redactor = secret_redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "thread": record.threadName,
            "location": f"{record.module}:{record.funcName}:{record.lineno}",
        }
        if record.exc_text:
            payload["exc"] = record.exc_text
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        if extras:
            payload["extra"] = extras
        line = json.dumps(payload, ensure_ascii=False, default=str)
        return self._redactor.redact(line)


class ConsoleFormatter(logging.Formatter):
    def __init__(self, secret_redactor: SecretRedactor) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        self._redactor = secret_redactor

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.redact(super().format(record))


class _CapturingQueueHandler(QueueHandler):
    """Prepara o registro na thread de origem: mensagem final, traceback e correlation_id."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        record = copy.copy(record)
        record.message = record.getMessage()
        record.msg = record.message
        record.args = None
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        correlation_id = current_correlation_id()
        record.correlation_id = str(correlation_id) if correlation_id else None
        return record


@dataclass(frozen=True)
class LoggingOptions:
    level: str = "INFO"
    module_levels: Mapping[str, str] = field(default_factory=dict)
    log_dir: Path | None = None
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5
    console: bool = True


class LoggingRuntime:
    """Mantém o listener em execução. Chame ``shutdown()`` ao encerrar a aplicação."""

    def __init__(self, listener: QueueListener, handler: QueueHandler, log_file: Path | None):
        self._listener = listener
        self._handler = handler
        self.log_file = log_file

    def shutdown(self) -> None:
        root = logging.getLogger()
        if self._handler in root.handlers:
            root.removeHandler(self._handler)
        self._listener.stop()
        for handler in self._listener.handlers:
            handler.close()


def configure_logging(
    options: LoggingOptions | None = None,
    *,
    secret_redactor: SecretRedactor = redactor,
) -> LoggingRuntime:
    options = options or LoggingOptions()
    sinks: list[logging.Handler] = []
    log_file: Path | None = None

    if options.log_dir is not None:
        options.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = options.log_dir / LOG_FILE_NAME
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=options.max_bytes,
            backupCount=options.backup_count,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(JsonFormatter(secret_redactor))
        sinks.append(file_handler)

    if options.console:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(ConsoleFormatter(secret_redactor))
        sinks.append(console)

    records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = _CapturingQueueHandler(records)
    listener = QueueListener(records, *sinks, respect_handler_level=True)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(queue_handler)
    apply_levels(options.level, options.module_levels)

    listener.start()
    return LoggingRuntime(listener, queue_handler, log_file)


_configured_loggers: set[str] = set()


def apply_levels(level: str, module_levels: Mapping[str, str]) -> None:
    """Aplica o nível global e por módulo; loggers removidos da lista voltam a herdar."""
    logging.getLogger().setLevel(level.upper())
    for name in _configured_loggers - set(module_levels):
        logging.getLogger(name).setLevel(logging.NOTSET)
    for name, module_level in module_levels.items():
        logging.getLogger(name).setLevel(module_level.upper())
    _configured_loggers.clear()
    _configured_loggers.update(module_levels)
