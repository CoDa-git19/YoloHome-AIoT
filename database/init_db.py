from __future__ import annotations
import sqlite3
import os
from config.settings import DB_PATH, SCHEMA_PATH


def init_db() -> None:
    """Initialize the SQLite database using database/schema.sql.

    The schema is idempotent, so normal startup should preserve existing
    data. Tests can request a clean reset explicitly via YOLOHOME_RESET_DB.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    reset_db = os.getenv("YOLOHOME_RESET_DB", "").strip().lower() in {"1", "true", "yes"}
    if reset_db and DB_PATH.exists():
        try:
            DB_PATH.unlink()
        except OSError as exc:
            # Windows khóa file khi còn connection mở (WinError 32).
            # Schema idempotent nên vẫn chạy tiếp được, chỉ là không reset.
            print(f"[init_db] Không xóa được {DB_PATH.name}: {exc}")

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(schema_sql)
        conn.commit()


if __name__ == "__main__":
    init_db()
