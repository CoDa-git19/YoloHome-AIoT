import sqlite3

from config.settings import SCHEMA_PATH


def _create_temp_db(tmp_path):
    db_path = tmp_path / "test_yolohome.db"
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(schema_sql)
        conn.commit()

    return db_path


def _columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def test_command_log_has_lifecycle_columns(tmp_path):
    db_path = _create_temp_db(tmp_path)

    with sqlite3.connect(str(db_path)) as conn:
        columns = _columns(conn, "command_log")

    assert "started_at" in columns
    assert "completed_at" in columns
    assert "latency_ms" in columns


def test_face_log_has_snapshot_path(tmp_path):
    db_path = _create_temp_db(tmp_path)

    with sqlite3.connect(str(db_path)) as conn:
        columns = _columns(conn, "face_log")

    assert "snapshot_path" in columns


def test_sensor_log_table_exists_with_expected_columns(tmp_path):
    db_path = _create_temp_db(tmp_path)

    with sqlite3.connect(str(db_path)) as conn:
        columns = _columns(conn, "sensor_log")

    assert {
        "id",
        "timestamp",
        "sensor",
        "value",
        "unit",
        "room",
        "source",
        "raw_json",
    }.issubset(columns)


def test_command_log_starts_without_completed_at(tmp_path):
    db_path = _create_temp_db(tmp_path)

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            """
            INSERT INTO command_log
                (transcript, json_cmd, result, execution_status)
            VALUES (?, ?, ?, ?)
            """,
            ("mở cửa chính", "{}", "waiting_auth", "waiting_auth"),
        )
        command_id = cur.lastrowid

        row = conn.execute(
            """
            SELECT started_at, completed_at
            FROM command_log
            WHERE id = ?
            """,
            (command_id,),
        ).fetchone()

    assert row[0] is not None
    assert row[1] is None