import sqlite3

import pytest

import services.logging_service as logging_service_module
import services.rule_service as rule_service_module
from services.command_service import CommandService
from services.rule_service import RuleService


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """
    Use a temporary SQLite database for service tests.

    This avoids deleting database/yolohome.db used for demo/runtime.
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


def test_command_service_execute_flow():
    """CommandService handles normal device command."""
    cmd_service = CommandService(use_mock=True)

    result = cmd_service.handle_transcript("bật đèn phòng khách")

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["execution_status"] == "success"
    assert result["result"] == "success"
    assert result["command"]["device"] == "light"
    assert result["command"]["action"] == "turn_on"


def test_command_service_waiting_auth_flow():
    """CommandService returns waiting_auth for sensitive command."""
    cmd_service = CommandService(use_mock=True)

    result = cmd_service.handle_transcript("mở cửa chính")

    assert result["ok"] is True
    assert result["next_step"] == "auth_required"
    assert result["execution_status"] == "waiting_auth"
    assert result["result"] == "waiting_auth"
    assert result["command"]["device"] == "door"
    assert result["command"]["action"] == "open"
    assert result["command"]["face_auth"] is True


def test_command_service_create_rule_flow():
    """CommandService creates automation rule through RuleService."""
    rule_service = RuleService()
    cmd_service = CommandService(rule_service=rule_service, use_mock=True)

    result = cmd_service.handle_transcript(
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách"
    )

    assert result["ok"] is True
    assert result["next_step"] == "create_rule"
    assert result["execution_status"] == "success"
    assert result["result"] == "success"

    active_rules = rule_service.get_active_rules()

    assert len(active_rules) == 1
    assert active_rules[0]["sensor"] == "temperature"
    assert active_rules[0]["operator"] == ">"
    assert active_rules[0]["value"] == 30.0
    assert active_rules[0]["device"] == "fan"
    assert active_rules[0]["action"] == "turn_on"
    assert active_rules[0]["room"] == "living_room"


@pytest.mark.parametrize(
    "transcript",
    [
        "bật máy lạnh phòng bếp",
        "bật tivi phòng khách",
    ],
)
def test_command_service_registry_request_flow(transcript):
    """CommandService handles unsupported room/device as registry request."""
    cmd_service = CommandService(use_mock=True)

    result = cmd_service.handle_transcript(transcript)

    assert result["ok"] is True
    assert result["next_step"] == "registry_request"
    assert result["execution_status"] == "registry_request"
    assert result["result"] == "waiting_admin_review"
    assert result["command"]["intent"] == "registry_request"


def test_rule_service_trigger_evaluation():
    """RuleService triggers action when sensor value matches condition."""
    rule_service = RuleService()

    mock_command = {
        "intent": "create_rule",
        "action": "turn_on",
        "device": "fan",
        "room": "living_room",
        "condition": {
            "sensor": "temperature",
            "operator": ">",
            "value": 30,
        },
    }

    rule_id = rule_service.create_rule(command_id=42, command=mock_command)

    assert rule_id > 0

    actions = rule_service.evaluate_sensor_data({"temperature": 28})
    assert actions == []

    actions = rule_service.evaluate_sensor_data({"temperature": 35})

    assert len(actions) == 1
    assert actions[0]["device"] == "fan"
    assert actions[0]["action"] == "turn_on"
    assert actions[0]["room"] == "living_room"


def test_rule_service_accepts_zero_threshold():
    """RuleService should allow value=0 in condition."""
    rule_service = RuleService()

    mock_command = {
        "intent": "create_rule",
        "action": "turn_on",
        "device": "light",
        "room": "living_room",
        "condition": {
            "sensor": "light",
            "operator": "==",
            "value": 0,
        },
    }

    rule_id = rule_service.create_rule(command_id=1, command=mock_command)

    assert rule_id > 0

    actions = rule_service.evaluate_sensor_data({"light": 0})

    assert len(actions) == 1
    assert actions[0]["device"] == "light"
    assert actions[0]["action"] == "turn_on"