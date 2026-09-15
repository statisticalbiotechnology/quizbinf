"""Copy every row from one database into another.

Two callers, one direction each, and they are the same operation:

* moving this app off SQLite and onto a PostgreSQL server;
* `GET /api/backup.zip` on a deployment whose database *is* PostgreSQL,
  where `VACUUM INTO` has nothing to work on and the archive would otherwise
  be refused.

Copying through SQLAlchemy's metadata rather than a vendor dump is what makes
one implementation serve both. It also means the backup of a Postgres
deployment is an ordinary SQLite file: openable by anybody, checkable with
`PRAGMA integrity_check`, restorable onto either backend, and needing no
`pg_dump` binary in the image to match the server's version.

What it therefore is *not*: a complete PostgreSQL dump. It carries the rows of
the tables this application defines, and nothing else — no roles, no grants,
no objects some future migration adds outside the ORM. For a
restore-the-server backup, `pg_dump` on the database host is the right tool
and this is not a substitute for it. This is the app's own portable copy of
the only data that cannot be recreated.
"""

import logging

from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.engine import Engine

from . import models  # noqa: F401 - registers every table on the metadata
from .db import Base

log = logging.getLogger("quizbinf.dbcopy")

#: Rows held in memory at once. Large enough that a lecture's answers move in
#: a handful of round trips, small enough that a term of them does not have to
#: fit in memory at all.
BATCH = 500


class CopyRefused(Exception):
    """The copy would have destroyed or mixed data, so it did not start."""


def _row_counts(engine: Engine) -> dict[str, int]:
    counts = {}
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            counts[table.name] = connection.scalar(
                select(func.count()).select_from(table)
            )
    return counts


def _reset_sequences(engine: Engine) -> None:
    """Point each identity sequence past the ids that were just inserted.

    The rows keep their primary keys, because answers reference round ids and
    rounds reference question ids — renumbering would either break those or
    require rewriting every reference. PostgreSQL's sequences know nothing
    about rows inserted with an explicit id, so without this the very next
    INSERT reuses id 1 and fails on the primary key. It is the classic way a
    migration looks perfect and then breaks on the first new row, which here
    would be the first student to answer in the first lecture afterwards.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            for column in table.primary_key.columns:
                sequence = connection.scalar(
                    text("SELECT pg_get_serial_sequence(:t, :c)"),
                    {"t": table.name, "c": column.name},
                )
                if not sequence:
                    continue
                connection.execute(
                    text(
                        f"SELECT setval('{sequence}', "  # noqa: S608 - name from the server
                        f"COALESCE((SELECT MAX({column.name}) FROM {table.name}), 0) + 1, "
                        "false)"
                    )
                )


def copy_database(source_url: str, target_url: str, *, force: bool = False) -> dict:
    """Copy every table from `source_url` into `target_url`.

    The target has its schema created if it does not have one, and must be
    empty unless `force` — copying into a database that already holds rows
    would merge two datasets on colliding primary keys, which is not something
    anybody wants and not something that can be undone.
    """
    if source_url == target_url:
        raise CopyRefused("source and target are the same database")

    source = create_engine(source_url)
    target = create_engine(target_url)
    try:
        Base.metadata.create_all(bind=target)

        existing = _row_counts(target)
        occupied = {name: n for name, n in existing.items() if n}
        if occupied and not force:
            raise CopyRefused(
                f"the target already holds rows ({occupied}); copying into it would "
                "merge two datasets on colliding ids. Empty it first, or pass force."
            )

        moved = {}
        for table in Base.metadata.sorted_tables:
            rows_copied = 0
            with source.connect() as reader:
                result = reader.execution_options(stream_results=True).execute(
                    select(table)
                )
                with target.begin() as writer:
                    while True:
                        batch = result.fetchmany(BATCH)
                        if not batch:
                            break
                        writer.execute(
                            insert(table), [dict(row._mapping) for row in batch]
                        )
                        rows_copied += len(batch)
            moved[table.name] = rows_copied
            log.info("copied %s: %d rows", table.name, rows_copied)

        _reset_sequences(target)

        # Count both ends rather than trusting the loop. A copy that silently
        # moved nothing is the failure that matters, and it looks exactly like
        # success from the inside.
        before = _row_counts(source)
        after = _row_counts(target)
        disagreed = {
            name: (before[name], after[name])
            for name in before
            if before[name] != after[name]
        }
        if disagreed:
            raise CopyRefused(f"source and target disagree after copying: {disagreed}")

        return {"tables": moved, "rows": sum(moved.values())}
    finally:
        source.dispose()
        target.dispose()
