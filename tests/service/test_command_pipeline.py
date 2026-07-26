import sqlite3

import pytest

import database.init_db as init_db_module
import services.logging_service as logging_service_module
from modules.llm_integration.llm_module import parse_and_validate
from services.logging_service import log_command


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Use a temporary SQLite database for command pipeline tests."""
    test_db_path = tmp_path / "test_yolohome.db"

    monkeypatch.setattr(init_db_module, "DB_PATH", test_db_path)
    monkeypatch.setattr(logging_service_module, "DB_PATH", test_db_path)

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
            """
        )

    yield

def test_command_pipeline_execute_flow():
    """Kiểm tra luồng pipeline cho lệnh điều khiển thông thường."""
    transcript = "bật đèn phòng khách"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "execute"
    assert llm_result["command"]["device"] == "light"
    assert llm_result["command"]["action"] == "turn_on"

    row_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="success",
        validation_status="passed",
        execution_status="success",
        latency_ms=llm_result["latency_ms"],
    )

    assert row_id > 0


def test_command_pipeline_auth_required_flow():
    """Kiểm tra luồng pipeline yêu cầu xác thực khuôn mặt."""
    transcript = "mở cửa chính"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "auth_required"
    assert llm_result["command"]["device"] == "door"
    assert llm_result["command"]["action"] == "open"
    assert llm_result["command"]["face_auth"] is True

    row_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="waiting_auth",
        validation_status="passed",
        execution_status="waiting_auth",
        latency_ms=llm_result["latency_ms"],
    )

    assert row_id > 0


def test_command_pipeline_create_rule_flow():
    """Kiểm tra luồng pipeline tạo luật tự động hóa."""
    transcript = "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "create_rule"
    assert llm_result["command"]["intent"] == "create_rule"
    assert llm_result["command"]["condition"]["sensor"] == "temperature"

    row_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="success",
        validation_status="passed",
        execution_status="success",
        latency_ms=llm_result["latency_ms"],
    )

    assert row_id > 0


def test_command_pipeline_registry_request_flow():
    """Kiểm tra luồng pipeline khi lệnh chứa phòng/thiết bị chưa đăng ký."""
    transcript = "bật máy lạnh phòng bếp"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "registry_request"
    assert llm_result["command"]["intent"] == "registry_request"

    command_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="waiting_admin_review",
        validation_status="passed",
        execution_status="registry_request",
        latency_ms=llm_result["latency_ms"],
        error_message=None,
    )

    assert command_id > 0