"""Engine SQLite, migrações automáticas e execução serializada fora do event loop."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from alfred.domain.module import Health, HealthStatus, Module

logger = logging.getLogger(__name__)

DB_FILE = "alfred.db"
SLOW_CLOSE_SECONDS = 2.0
MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


class Database(Module):
    """Banco local. Todo acesso passa por ``run()``, executado numa única thread dedicada.

    A thread única serializa as escritas no SQLite e mantém operações bloqueantes fora do event
    loop.
    """

    name = "database"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.url = f"sqlite:///{path.as_posix()}"
        self._executor: ThreadPoolExecutor | None = None
        self._engine: Engine | None = None
        self._sessions: sessionmaker[Session] | None = None
        self._current: str | None = None
        self._thread_id: int | None = None

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alfred-db")
        await self._submit(self._open)

    async def stop(self) -> None:
        if self._executor is None:
            return
        closing = asyncio.ensure_future(self._submit(self._close))
        done, _ = await asyncio.wait({closing}, timeout=SLOW_CLOSE_SECONDS)
        if not done:
            # Diagnóstico: o encerramento chegou a passar do limite em uso real sem reproduzir.
            logger.warning(
                "Banco demorando para fechar; a thread está em %s:\n%s",
                self._current or "(fila)",
                self._thread_stack(),
            )
        await closing
        self._executor.shutdown(wait=False)  # o close já terminou; não bloqueia o event loop
        self._executor = None

    async def health(self) -> Health:
        try:
            await self.run(lambda session: session.connection().exec_driver_sql("SELECT 1"))
        except Exception as exc:
            return Health(status=HealthStatus.UNHEALTHY, detail=repr(exc))
        return Health(status=HealthStatus.HEALTHY)

    # ------------------------------------------------------------------ acesso
    async def run[T](self, work: Callable[[Session], T]) -> T:
        """Executa ``work`` numa sessão transacional (commit no sucesso, rollback no erro)."""

        def call() -> T:
            if self._sessions is None:
                raise RuntimeError("Banco de dados não iniciado")
            with self._sessions.begin() as session:
                return work(session)

        return await self._submit(call)

    def current_revision(self) -> str | None:
        """Revisão do schema aplicada (uso síncrono em testes e ferramentas)."""
        if self._engine is None:
            raise RuntimeError("Banco de dados não iniciado")
        with self._engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    # ------------------------------------------------------------------ internos
    async def _submit[T](self, fn: Callable[[], T]) -> T:
        if self._executor is None:
            raise RuntimeError("Banco de dados não iniciado")

        def tracked() -> T:
            self._current = getattr(fn, "__qualname__", repr(fn))
            self._thread_id = threading.get_ident()
            try:
                return fn()
            finally:
                self._current = None

        return await asyncio.get_running_loop().run_in_executor(self._executor, tracked)

    def _thread_stack(self) -> str:
        frame = sys._current_frames().get(self._thread_id or -1)
        return "".join(traceback.format_stack(frame)[-8:]) if frame is not None else "(sem pilha)"

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        engine = create_engine(self.url)
        event.listen(engine, "connect", _configure_sqlite)

        config = alembic_config(self.url)
        head = ScriptDirectory.from_config(config).get_current_head()
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()

        if current != head:
            if existed:
                backup = self._backup(f"pre-{head}")
                logger.info("Backup do banco antes da migração: %s", backup.name)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            logger.info("Schema migrado de %s para %s", current, head)

        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def _close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._sessions = None

    def _backup(self, label: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.{label}-{stamp}.bak")
        with sqlite3.connect(self.path) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        return target
