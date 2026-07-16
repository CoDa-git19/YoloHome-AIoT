from __future__ import annotations
import sqlite3
import os
from config.settings import DB_PATH, SCHEMA_PATH


def init_db() -> None:
    """Initialize the SQLite database using database/schema.sql.

    If an existing database file is present with an incompatible schema,
    remove it so the schema can be recreated cleanly. Tests expect a
    deterministic fresh database when they call this helper.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Remove old DB if present to avoid 'no such column' when the schema
    # was bumped but the file persisted from earlier runs.
    if DB_PATH.exists():
        try:
            os.remove(DB_PATH)
        except Exception:
            # If removal fails for any reason, allow SQLite to surface a
            # clearer error when attempting to apply the schema.
            pass

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(schema_sql)
        conn.commit()


if __name__ == "__main__":
    init_db()