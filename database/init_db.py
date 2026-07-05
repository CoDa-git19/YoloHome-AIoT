import sqlite3
from config.settings import DB_PATH

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS command_log (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                transcript         TEXT    NOT NULL,
                json_cmd           TEXT    NOT NULL,
                intent             TEXT,
                action             TEXT,
                device             TEXT,
                room               TEXT,
                face_auth          INTEGER NOT NULL DEFAULT 0,
                validation_status  TEXT,
                execution_status   TEXT,
                result             TEXT    NOT NULL,
                latency_ms         INTEGER,
                error_message      TEXT
            );

            CREATE TABLE IF NOT EXISTS face_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                command_id    INTEGER,
                person_name   TEXT    NOT NULL,
                confidence    REAL    NOT NULL,
                status        TEXT    NOT NULL,
                triggered_by  TEXT,
                device        TEXT,
                room          TEXT,
                action_result TEXT,
                FOREIGN KEY (command_id) REFERENCES command_log(id)
            );

            CREATE TABLE IF NOT EXISTS schedule (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at    TEXT    NOT NULL,
                json_cmd  TEXT    NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS error_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                module    TEXT    NOT NULL,
                message   TEXT    NOT NULL
            );
        """)
