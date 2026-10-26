"""archive quizzes

Revision ID: f2b6d8a4c937
Revises: e4a9c2d7b815
Create Date: 2026-10-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b6d8a4c937'
down_revision: Union[str, Sequence[str], None] = 'e4a9c2d7b815'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A quiz can be archived. Null for every existing quiz: none is archived
    until the teacher says so."""
    op.add_column(
        "quizzes", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("quizzes", "archived_at")
