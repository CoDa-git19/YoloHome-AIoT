from __future__ import annotations
import sqlite3
from config.settings import DB_PATH, SCHEMA_PATH

def init_db() -> None:
    """Initialize the SQLite database using database/schema.sql."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(schema_sql)
        conn.commit()

if __name__ == "__main__":
    init_db()