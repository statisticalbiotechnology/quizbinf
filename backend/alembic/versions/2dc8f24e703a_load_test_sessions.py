"""load-test sessions

Revision ID: 2dc8f24e703a
Revises: 6eb543e0a5d5
Create Date: 2026-09-10 10:17:03.606647

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2dc8f24e703a'
down_revision: Union[str, Sequence[str], None] = '6eb543e0a5d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Mark a session as a rehearsal rather than a lecture.

    `sa.false()` rather than the `sa.text('0')` autogenerate produced: 0 is a
    boolean only in SQLite, and Postgres — which docker-compose runs and which
    this app is meant to move to — refuses an integer default on a boolean
    column. Every existing row is a real lecture.
    """
    op.add_column(
        "sessions",
        sa.Column("is_loadtest", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sessions", "is_loadtest")
