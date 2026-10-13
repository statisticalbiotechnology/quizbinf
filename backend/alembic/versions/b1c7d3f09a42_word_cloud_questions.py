"""word-cloud questions

Revision ID: b1c7d3f09a42
Revises: 2dc8f24e703a
Create Date: 2026-10-13 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c7d3f09a42'
down_revision: Union[str, Sequence[str], None] = '2dc8f24e703a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A second kind of question, answered with a word instead of a choice.

    Every question that exists is multiple choice, hence the server default:
    the column has to be readable for rows written before it existed, and the
    app's own default never reaches them.

    `choice_id` becomes nullable because a word-cloud answer has no choice to
    point at, and the check constraint is what stops that permissiveness from
    meaning "an answer may have neither". Written in a batch block so SQLite
    rebuilds the table rather than refusing to alter it — the deployment is
    Postgres, but a developer's database is not.
    """
    kind = sa.Enum("choice", "text", name="kind")
    kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "questions",
        sa.Column("kind", kind, nullable=False, server_default="choice"),
    )
    with op.batch_alter_table("answers") as batch:
        batch.add_column(sa.Column("text", sa.String(length=40), nullable=True))
        batch.alter_column("choice_id", existing_type=sa.Integer(), nullable=True)
        batch.create_check_constraint(
            "ck_answer_is_choice_or_text", "(choice_id IS NULL) <> (text IS NULL)"
        )


def downgrade() -> None:
    """Word-cloud answers have no choice to fall back to, so they go with the
    column that held them; a downgrade is for a schema, not for data."""
    op.execute("DELETE FROM answers WHERE choice_id IS NULL")
    with op.batch_alter_table("answers") as batch:
        batch.drop_constraint("ck_answer_is_choice_or_text", type_="check")
        batch.alter_column("choice_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("text")
    op.drop_column("questions", "kind")
    sa.Enum(name="kind").drop(op.get_bind(), checkfirst=True)
