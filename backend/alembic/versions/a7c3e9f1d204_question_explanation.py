"""question explanation

Revision ID: a7c3e9f1d204
Revises: f2b6d8a4c937
Create Date: 2026-10-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1d204'
down_revision: Union[str, Sequence[str], None] = 'f2b6d8a4c937'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Each question may carry an explanation of its answer. Empty for every
    question that exists."""
    op.add_column(
        "questions",
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("questions", "explanation")
