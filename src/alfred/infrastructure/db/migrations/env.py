"""Ambiente do Alembic. Usa a conexão fornecida pelo ``Database`` ou a URL do alembic.ini."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, create_engine

from alfred.infrastructure.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # ALTER TABLE no SQLite
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url não configurada")
    with create_engine(url).connect() as own_connection:
        _run(own_connection)
        own_connection.commit()
