"""Origem e confiança das memórias aprendidas (#120).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("conversation_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("replaces_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_column("replaces_id")
        batch_op.drop_column("conversation_id")
        batch_op.drop_column("confidence")
