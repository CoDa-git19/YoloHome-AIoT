import sqlite3

import pytest

import services.logging_service as logging_service_module
import services.rule_service as rule_service_module
from services.command_service import CommandService
from system_core.commands import (
    CloseDoorCommand,
    GetStatusCommand,
    TurnOnLightCommand,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """
    Use a temporary SQLite database for Command Pattern tests.

    These tests call CommandService.handle_transcript(), which writes to command_log.
    Using tmp_path prevents tests from touching database/yolohome.db used for demo/runtime.
    """
    test_db_path = tmp_path / "test_yolohome.db"

    monkeypatch.setattr(logging_service_module, "DB_PATH", test_db_path)
    monkeypatch.setattr(rule_service_module, "DB_PATH", test_db_path)

    with sqlite3.connect(str(test_db_path)) as conn:
        conn.executescript(
            """
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

            CREATE TABLE IF NOT EXISTS error_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                module    TEXT    NOT NULL,
                message   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS automation_rules (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id  INTEGER,
                sensor      TEXT    NOT NULL,
                operator    TEXT    NOT NULL,
                value       REAL    NOT NULL,
                action      TEXT    NOT NULL,
                device      TEXT    NOT NULL,
                room        TEXT    NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (command_id) REFERENCES command_log(id)
            );
            """
        )

    yield


def test_create_turn_on_light_command():
    service = CommandService(use_mock=True)

    command = service.create_command(
        {
            "intent": "control_device",
            "action": "turn_on",
            "device": "light",
            "room": "living_room",
            "face_auth": False,
            "condition": None,
            "response": "Đã bật đèn phòng khách.",
        }
    )

    assert isinstance(command, TurnOnLightCommand)


def test_create_close_door_command():
    service = CommandService(use_mock=True)

    command = service.create_command(
        {
            "intent": "control_device",
            "action": "close",
            "device": "door",
            "room": "main_door",
            "face_auth": False,
            "condition": None,
            "response": "Đã đóng cửa chính.",
        }
    )

    assert isinstance(command, CloseDoorCommand)


def test_create_get_status_command():
    service = CommandService(use_mock=True)

    command = service.create_command(
        {
            "intent": "query_status",
            "action": "get_status",
            "device": "fan",
            "room": "bedroom",
            "face_auth": False,
            "condition": None,
            "response": "Đang kiểm tra trạng thái quạt.",
        }
    )

    assert isinstance(command, GetStatusCommand)


def test_execute_command_is_added_to_history():
    service = CommandService(use_mock=True)

    result = service.handle_transcript("bật đèn phòng khách")

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["result"] == "success"
    assert len(service.command_history) == 1
    assert isinstance(service.command_history[0], TurnOnLightCommand)


def test_auth_required_command_is_not_executed_yet():
    service = CommandService(use_mock=True)

    result = service.handle_transcript("mở cửa chính")

    assert result["ok"] is True
    assert result["next_step"] == "auth_required"
    assert result["result"] == "waiting_auth"
    assert len(service.command_history) == 0


def test_undo_last_command():
    service = CommandService(use_mock=True)

    result = service.handle_transcript("bật đèn phòng khách")

    assert result["ok"] is True
    assert len(service.command_history) == 1

    undo_result = service.undo_last_command()

    assert undo_result is True
    assert len(service.command_history) == 0