"""Move this app's data between databases.

The move it exists for is SQLite on a mounted volume to a PostgreSQL server:

    python -m tools.copy_database \
        --from "sqlite:////home/data/quizbinf.db" \
        --to   "postgresql+psycopg://quizbinf:…@heisenberg.scilifelab.se/quizbinf?sslmode=require"

It reads the source and writes the target; it never alters the source, so the
SQLite file remains a working fallback until the new one is proven. Run it
against a *stopped* app, or at least one nobody is answering on: rows written
after the copy starts are not in it.

The uploaded figures are files rather than rows and do not move with this —
they live in `<data>/images/` and go wherever the app's data directory goes.
"""

import argparse
import json
import sys

from app.config import get_settings
from app.dbcopy import CopyRefused, copy_database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="source",
        default=None,
        help="source URL; defaults to this checkout's configured DATABASE_URL",
    )
    parser.add_argument("--to", dest="target", required=True, help="target URL")
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "copy even though the target already holds rows. This merges two "
            "datasets on colliding primary keys and cannot be undone."
        ),
    )
    args = parser.parse_args()

    source = args.source or get_settings().resolved_database_url
    print(f"from: {source.split('://')[0]}://…\nto:   {args.target.split('://')[0]}://…")
    try:
        moved = copy_database(source, args.target, force=args.force)
    except CopyRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2

    print(json.dumps(moved["tables"], indent=2))
    print(f"\n{moved['rows']} rows copied, and both ends agree on every table.")
    print(
        "\nNext: point DATABASE_URL at the target and restart. Keep the source "
        "until a lecture has run against the new database."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
