import sqlite3

import services.logging_service as logging_module
from services.logging_service import LoggingService


def _init_sensor_db(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE sensor_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
                sensor    TEXT NOT NULL,
                value     REAL,
                unit      TEXT,
                room      TEXT,
                source    TEXT,
                raw_json  TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE error_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                module    TEXT NOT NULL,
                message   TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _fetch_sensor_rows(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT sensor, value, unit, room, source, raw_json
            FROM sensor_log
            ORDER BY id ASC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def test_log_sensor_data_flat_format(tmp_path, monkeypatch):
    db_path = tmp_path / "sensor_flat.db"
    _init_sensor_db(db_path)
    monkeypatch.setattr(logging_module, "DB_PATH", db_path)

    service = LoggingService()
    service.log_sensor_data(
        {
            "temperature": 30,
            "humidity": 55,
            "room": "living_room",
            "source": "mock",
        }
    )

    rows = _fetch_sensor_rows(db_path)

    assert len(rows) == 2
    assert rows[0]["sensor"] == "temperature"
    assert rows[0]["value"] == 30
    assert rows[0]["room"] == "living_room"
    assert rows[0]["source"] == "mock"
    assert rows[1]["sensor"] == "humidity"
    assert rows[1]["value"] == 55


def test_log_sensor_data_nested_format(tmp_path, monkeypatch):
    db_path = tmp_path / "sensor_nested.db"
    _init_sensor_db(db_path)
    monkeypatch.setattr(logging_module, "DB_PATH", db_path)

    service = LoggingService()
    service.log_sensor_data(
        {
            "room": "bedroom",
            "source": "hardware",
            "readings": {
                "temperature": {"value": 28.5, "unit": "C"},
                "light": {"value": 720, "unit": "lux"},
            },
        }
    )

    rows = _fetch_sensor_rows(db_path)

    assert len(rows) == 2
    assert rows[0]["sensor"] == "temperature"
    assert rows[0]["value"] == 28.5
    assert rows[0]["unit"] == "C"
    assert rows[0]["room"] == "bedroom"
    assert rows[0]["source"] == "hardware"
    assert rows[1]["sensor"] == "light"
    assert rows[1]["value"] == 720
    assert rows[1]["unit"] == "lux"


def test_log_sensor_data_single_reading_format(tmp_path, monkeypatch):
    db_path = tmp_path / "sensor_single.db"
    _init_sensor_db(db_path)
    monkeypatch.setattr(logging_module, "DB_PATH", db_path)

    service = LoggingService()
    service.log_sensor_data(
        {
            "sensor": "motion",
            "value": 1,
            "unit": "bool",
            "room": "main_door",
            "source": "pir",
        }
    )

    rows = _fetch_sensor_rows(db_path)

    assert len(rows) == 1
    assert rows[0]["sensor"] == "motion"
    assert rows[0]["value"] == 1
    assert rows[0]["unit"] == "bool"
    assert rows[0]["room"] == "main_door"
    assert rows[0]["source"] == "pir"


def test_logging_service_update_logs_sensor_data(tmp_path, monkeypatch):
    db_path = tmp_path / "sensor_update.db"
    _init_sensor_db(db_path)
    monkeypatch.setattr(logging_module, "DB_PATH", db_path)

    service = LoggingService()
    service.update(
        {
            "temperature": 31,
            "room": "living_room",
            "source": "observer_test",
        }
    )

    rows = _fetch_sensor_rows(db_path)

    assert len(rows) == 1
    assert rows[0]["sensor"] == "temperature"
    assert rows[0]["value"] == 31
    assert rows[0]["source"] == "observer_test"