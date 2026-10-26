"""several words per word-cloud answer

Revision ID: e4a9c2d7b815
Revises: b1c7d3f09a42
Create Date: 2026-10-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4a9c2d7b815'
down_revision: Union[str, Sequence[str], None] = 'b1c7d3f09a42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A word cloud may ask each student for more than one entry.

    The count lives on the question, defaulting to one so every existing
    cloud keeps asking what it asked. The entries share the student's one
    answer row, one per line, so the column widens to hold five of them.
    Widening only: every value already stored fits.
    """
    op.add_column(
        "questions",
        sa.Column("max_words", sa.Integer(), nullable=False, server_default="1"),
    )
    with op.batch_alter_table("answers") as batch:
        batch.alter_column(
            "text", existing_type=sa.String(length=40), type_=sa.String(length=205)
        )


def downgrade() -> None:
    """Only the first entry of a several-word answer survives the narrower
    column; a downgrade is for a schema, not for data."""
    if op.get_bind().dialect.name == "sqlite":
        first = "substr(text, 1, instr(text || char(10), char(10)) - 1)"
    else:
        first = "split_part(text, chr(10), 1)"
    op.execute(f"UPDATE answers SET text = {first} WHERE text IS NOT NULL")
    with op.batch_alter_table("answers") as batch:
        batch.alter_column(
            "text", existing_type=sa.String(length=205), type_=sa.String(length=40)
        )
    op.drop_column("questions", "max_words")
