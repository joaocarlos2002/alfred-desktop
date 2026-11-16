from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from alfred.domain.events import correlation_scope
from alfred.infrastructure.logging_setup import (
    REDACTED,
    LoggingOptions,
    LoggingRuntime,
    SecretRedactor,
    configure_logging,
)
from alfred.infrastructure.paths import DATA_DIR_ENV, data_dir, logs_dir


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


def start(tmp_path: Path, redactor: SecretRedactor | None = None, **kw: object) -> LoggingRuntime:
    options = LoggingOptions(log_dir=tmp_path, console=False, **kw)  # type: ignore[arg-type]
    return configure_logging(options, secret_redactor=redactor or SecretRedactor())


def read_lines(runtime: LoggingRuntime) -> list[dict[str, object]]:
    runtime.shutdown()
    assert runtime.log_file is not None
    return [json.loads(line) for line in runtime.log_file.read_text("utf-8").splitlines()]


def test_writes_structured_json_with_correlation_id(tmp_path: Path) -> None:
    runtime = start(tmp_path)
    log = logging.getLogger("alfred.test")
    cid = uuid4()

    log.info("fora da cadeia")
    with correlation_scope(cid):
        log.warning("tarefa %s concluída", "abrir bloco de notas", extra={"step": 2})

    first, second = read_lines(runtime)
    assert first["correlation_id"] is None
    assert second["correlation_id"] == str(cid)
    assert second["level"] == "WARNING"
    assert second["logger"] == "alfred.test"
    assert second["message"] == "tarefa abrir bloco de notas concluída"
    assert second["extra"] == {"step": 2}
    assert str(second["ts"]).endswith("+00:00")


def test_exception_traceback_is_captured(tmp_path: Path) -> None:
    runtime = start(tmp_path)
    try:
        raise ValueError("falhou")
    except ValueError:
        logging.getLogger("alfred.test").exception("erro")

    (line,) = read_lines(runtime)
    assert "ValueError: falhou" in str(line["exc"])


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("chave sk-abcdefghijklmnopqrstuvwx usada", "sk-abcdefghijklmnopqrstuvwx"),
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
        ("password=hunter22 e mais", "hunter22"),
        ("senha: minhaSenha!", "minhaSenha!"),
        ("token ghp_aaaaaaaaaaaaaaaaaaaaaaaa1234", "ghp_aaaaaaaaaaaaaaaaaaaaaaaa1234"),
        ('{"api_key": "xyz-123-abc"}', "xyz-123-abc"),
    ],
)
def test_known_secret_patterns_are_masked(text: str, secret: str) -> None:
    redacted = SecretRedactor().redact(text)
    assert secret not in redacted
    assert REDACTED in redacted


def test_secrets_are_masked_in_message_args_extras_and_traceback(tmp_path: Path) -> None:
    redactor = SecretRedactor()
    redactor.register("valor-registrado-123")
    runtime = start(tmp_path, redactor)
    log = logging.getLogger("alfred.test")

    log.info("credencial %s", "valor-registrado-123")
    log.info("extra", extra={"headers": {"Authorization": "Bearer tok.en.value"}})
    try:
        raise RuntimeError("api_key=supersecreta")
    except RuntimeError:
        log.exception("falha")

    raw = json.dumps(read_lines(runtime))
    for secret in ("valor-registrado-123", "tok.en.value", "supersecreta"):
        assert secret not in raw


def test_module_levels_are_respected(tmp_path: Path) -> None:
    runtime = start(tmp_path, level="WARNING", module_levels={"alfred.voice": "DEBUG"})

    logging.getLogger("alfred.core").info("oculto")
    logging.getLogger("alfred.voice").debug("visível")

    lines = read_lines(runtime)
    assert [line["message"] for line in lines] == ["visível"]
    logging.getLogger("alfred.voice").setLevel(logging.NOTSET)


def test_rotation_keeps_configured_backups(tmp_path: Path) -> None:
    runtime = start(tmp_path, max_bytes=2_000, backup_count=2)
    log = logging.getLogger("alfred.test")
    for i in range(200):
        log.info("linha %d %s", i, "x" * 50)
    runtime.shutdown()

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["alfred.log", "alfred.log.1", "alfred.log.2"]


def test_data_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    assert data_dir() == tmp_path.resolve()
    assert logs_dir() == tmp_path.resolve() / "logs"


def test_data_dir_defaults_to_local_app_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "ALFRED"
