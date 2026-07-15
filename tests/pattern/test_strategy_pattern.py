from __future__ import annotations

import sqlite3
from typing import Any

import pytest

import services.logging_service as logging_service_module
import services.rule_service as rule_service_module
from modules.llm_integration.llm_strategy import GeminiLLMStrategy, MockLLMStrategy
from services.command_service import CommandService
from system_core.strategies import LLMStrategy


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """
    Use a temporary SQLite database for Strategy Pattern tests.

    CommandService.handle_transcript() writes to command_log,
    so tests must not touch database/yolohome.db.
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


class FakeLLMStrategy(LLMStrategy):
    """Fake LLM strategy for testing dependency injection."""

    def __init__(self) -> None:
        self.called = False
        self.last_transcript: str | None = None
        self.last_sensor_data: dict[str, Any] | None = None

    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        pending_command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.called = True
        self.last_transcript = transcript
        self.last_sensor_data = sensor_data

        return {
            "ok": True,
            "transcript": transcript,
            "command": {
                "intent": "control_device",
                "action": "turn_on",
                "device": "light",
                "room": "living_room",
                "face_auth": False,
                "condition": None,
                "response": "Fake strategy đã bật đèn.",
            },
            "validation": {
                "passed": True,
                "code": "valid",
                "message": "Fake valid command.",
            },
            "next_step": "execute",
            "latency_ms": 1,
            "log_result": None,
            "error": None,
        }


class FakeRejectLLMStrategy(LLMStrategy):
    """Fake LLM strategy returning a rejected command."""

    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        pending_command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "transcript": transcript,
            "command": {
                "intent": "reject",
                "action": None,
                "device": None,
                "room": None,
                "face_auth": False,
                "condition": None,
                "response": "Fake strategy từ chối lệnh.",
            },
            "validation": {
                "passed": True,
                "code": "reject",
                "message": "No hardware execution required.",
            },
            "next_step": "reject",
            "latency_ms": 1,
            "log_result": "rejected: unknown_device",
            "error": None,
        }


def test_command_service_uses_injected_llm_strategy():
    fake_strategy = FakeLLMStrategy()

    service = CommandService(
        llm_strategy=fake_strategy,
        use_mock=True,
    )

    result = service.handle_transcript("bất kỳ câu gì")

    assert fake_strategy.called is True
    assert fake_strategy.last_transcript == "bất kỳ câu gì"
    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["result"] == "success"
    assert result["command"]["response"] == "Fake strategy đã bật đèn."


def test_command_service_passes_sensor_data_to_llm_strategy():
    fake_strategy = FakeLLMStrategy()

    service = CommandService(
        llm_strategy=fake_strategy,
        use_mock=True,
    )

    sensor_data = {
        "temperature": 32,
        "humidity": 70,
    }

    result = service.handle_transcript(
        transcript="nhiệt độ hiện tại thế nào",
        sensor_data=sensor_data,
    )

    assert result["ok"] is True
    assert fake_strategy.called is True
    assert fake_strategy.last_sensor_data == sensor_data


def test_command_service_can_use_different_llm_strategy():
    service = CommandService(
        llm_strategy=FakeRejectLLMStrategy(),
        use_mock=True,
    )

    result = service.handle_transcript("bật máy lạnh phòng bếp")

    assert result["ok"] is False
    assert result["next_step"] == "reject"
    assert result["execution_status"] == "rejected"
    assert result["result"] == "rejected: unknown_device"
    assert result["command"]["response"] == "Fake strategy từ chối lệnh."


def test_default_llm_strategy_is_gemini_llm_strategy():
    service = CommandService(use_mock=True)

    assert isinstance(service.llm_strategy, GeminiLLMStrategy)


def test_mock_llm_strategy_parses_known_command():
    strategy = MockLLMStrategy()

    result = strategy.parse_and_validate("bật đèn phòng khách")

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["device"] == "light"
    assert result["command"]["action"] == "turn_on"
    assert result["command"]["room"] == "living_room"