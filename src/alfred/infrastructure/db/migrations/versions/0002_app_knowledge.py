"""Conhecimento sobre programas (skill #125).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import alfred.infrastructure.db.types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_knowledge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("elements", sa.JSON(), nullable=False),
        sa.Column("shortcuts", sa.JSON(), nullable=False),
        sa.Column("procedures", sa.JSON(), nullable=False),
        sa.Column(
            "explored_at", alfred.infrastructure.db.types.UTCDateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at", alfred.infrastructure.db.types.UTCDateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", alfred.infrastructure.db.types.UTCDateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_knowledge")),
        sa.UniqueConstraint("app_key", name=op.f("uq_app_knowledge_app_key")),
    )


def downgrade() -> None:
    op.drop_table("app_knowledge")
