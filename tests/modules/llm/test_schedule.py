"""
Test cho intent=schedule — lệnh điều khiển có độ trễ.

CHẠY OFFLINE: không gọi Gemini, không cần phần cứng.

QUY TẮC QUAN TRỌNG NHẤT ĐƯỢC KHOÁ LẠI Ở ĐÂY
-------------------------------------------
    Lệnh hẹn giờ chạy khi KHÔNG có ai đứng trước camera, nên hành động cần
    Face Auth không bao giờ được đặt lịch.

Đây chính xác là lý do automation rule đã bị chặn (validator ->
safety_rule_violation). Lịch hẹn giờ là một đường thực thi KHÔNG NGƯỜI thứ hai,
nên nó cần lớp phòng vệ tương đương. Thiếu chốt này thì "sau 5 phút mở cửa
chính" sẽ mở được cửa - mở lại đúng lỗ hổng mà Layer 3 đã đóng, qua một cửa mới.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from database import init_db as init_db_module
from modules.llm_integration.llm_module import load_device_registry
from modules.llm_integration.validator import (
    MAX_SCHEDULE_DELAY_SECONDS,
    MIN_SCHEDULE_DELAY_SECONDS,
    load_default_command_schema,
    validate_command,
)
from services import logging_service as logging_service_module
from services import rule_service as rule_service_module
from services.command_service import CommandService
from services.rule_service import RuleService
from system_core.observers import ScheduleObserver


REGISTRY = load_device_registry()
SCHEMA = load_default_command_schema()

BASE = {
    "intent": "schedule",
    "face_auth": False,
    "condition": None,
    "response": "Đã hẹn giờ.",
}
FAN = {**BASE, "action": "turn_on", "device": "fan", "room": "bedroom",
       "delay_seconds": 300}
DOOR = {**BASE, "action": "open", "device": "door", "room": "main_door",
        "delay_seconds": 300}


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_schedule.db"
    monkeypatch.setattr(init_db_module, "DB_PATH", test_db)
    monkeypatch.setattr(logging_service_module, "DB_PATH", test_db)
    monkeypatch.setattr(rule_service_module, "DB_PATH", test_db)
    init_db_module.init_db()
    yield test_db


class Recorder:
    """Thay cho CommandService ở phía thực thi; đếm số lệnh tới phần cứng."""

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def execute_authorized_command(self, command: dict[str, Any]) -> bool:
        self.commands.append(command)
        return True


class FakeStrategy:
    def __init__(self, command: dict[str, Any]) -> None:
        self.command = command
        self.calls = 0

    def parse_and_validate(self, transcript, sensor_data=None, pending_command=None):
        self.calls += 1
        return {
            "ok": True,
            "transcript": transcript,
            "command": dict(self.command),
            "validation": {"passed": True, "code": "valid"},
            "next_step": "create_schedule",
            "latency_ms": 0,
            "log_result": "success",
            "error": None,
        }


def build(command=FAN) -> CommandService:
    return CommandService(
        rule_service=RuleService(),
        llm_strategy=FakeStrategy(command),
        use_mock=True,
    )


# =============================================================================
# 1. AN NINH — nhóm quan trọng nhất
# =============================================================================

def test_a_sensitive_action_cannot_be_scheduled() -> None:
    """
    "sau 5 phút mở cửa chính" phải bị từ chối lúc TẠO.

    Không có chốt này, lịch hẹn giờ trở thành đường vòng qua Face Auth.
    """
    result = validate_command(DOOR, REGISTRY, SCHEMA)

    assert result["passed"] is False
    assert result["code"] == "safety_rule_violation"


def test_the_observer_blocks_a_sensitive_action_written_straight_to_the_table(
    isolated_db,
) -> None:
    """
    Lớp phòng vệ thứ hai: ai đó ghi thẳng vào bảng schedule, bỏ qua validator.

    Trùng lặp là CÓ CHỦ ĐÍCH. Lớp cuối trước khi phần cứng chạy không nên tin
    vào thiện chí của lớp trước.
    """
    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "INSERT INTO schedule (run_at, json_cmd) VALUES "
            "(datetime('now','localtime','-1 second'), ?)",
            (json.dumps({"action": "open", "device": "door", "room": "main_door"}),),
        )

    recorder = Recorder()
    observer = ScheduleObserver(recorder)

    observer.update({})

    assert recorder.commands == []
    assert observer.blocked_count == 1


def test_a_harmless_action_is_scheduled_normally() -> None:
    result = validate_command(FAN, REGISTRY, SCHEMA)

    assert result["passed"] is True


# =============================================================================
# 2. Kiểm tra độ trễ
# =============================================================================

@pytest.mark.parametrize(
    "delay",
    [None, "300", 300.5, True, -10, 0, MIN_SCHEDULE_DELAY_SECONDS - 1,
     MAX_SCHEDULE_DELAY_SECONDS + 1],
)
def test_invalid_delays_are_rejected(delay) -> None:
    command = {**FAN}
    if delay is None:
        command.pop("delay_seconds")
    else:
        command["delay_seconds"] = delay

    result = validate_command(command, REGISTRY, SCHEMA)

    assert result["passed"] is False
    assert result["code"] == "invalid_delay"


@pytest.mark.parametrize(
    "delay", [MIN_SCHEDULE_DELAY_SECONDS, 300, MAX_SCHEDULE_DELAY_SECONDS]
)
def test_valid_delays_are_accepted(delay) -> None:
    result = validate_command({**FAN, "delay_seconds": delay}, REGISTRY, SCHEMA)

    assert result["passed"] is True


def test_a_schedule_still_goes_through_the_registry_checks() -> None:
    """Hẹn giờ không phải cửa sau: phòng và thiết bị vẫn phải tồn tại."""
    result = validate_command(
        {**FAN, "room": "kitchen"}, REGISTRY, SCHEMA
    )

    assert result["passed"] is False
    assert result["code"] == "unknown_room"


# =============================================================================
# 3. Vòng đời một lịch hẹn
# =============================================================================

def test_the_command_service_writes_a_row_and_confirms(isolated_db) -> None:
    service = build()

    result = service.handle_transcript("sau 5 phút bật quạt phòng ngủ", session_id="s1")

    assert result["execution_status"] == "scheduled"
    assert "sau 5 phút" in result["response"]

    with sqlite3.connect(str(isolated_db)) as conn:
        rows = conn.execute("SELECT run_at, is_active FROM schedule").fetchall()

    assert len(rows) == 1
    assert rows[0][1] == 1


def test_nothing_runs_before_the_due_time(isolated_db) -> None:
    build().handle_transcript("sau 5 phút bật quạt phòng ngủ", session_id="s1")

    recorder = Recorder()
    ScheduleObserver(recorder).update({})

    assert recorder.commands == []


def test_the_command_runs_once_the_time_has_passed(isolated_db) -> None:
    build().handle_transcript("sau 5 phút bật quạt phòng ngủ", session_id="s1")

    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "UPDATE schedule SET run_at = datetime('now','localtime','-1 second')"
        )

    recorder = Recorder()
    ScheduleObserver(recorder).update({})

    assert len(recorder.commands) == 1
    assert recorder.commands[0]["device"] == "fan"
    assert recorder.commands[0]["room"] == "bedroom"
    assert recorder.commands[0]["intent"] == "control_device"


def test_a_schedule_never_fires_twice(isolated_db) -> None:
    """
    Vòng cảm biến chạy mỗi 2 giây. Không có chốt này, một lịch đã tới hạn sẽ
    chạy lại ở MỌI vòng cho tới khi bị xoá.
    """
    build().handle_transcript("sau 5 phút bật quạt phòng ngủ", session_id="s1")

    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "UPDATE schedule SET run_at = datetime('now','localtime','-1 second')"
        )

    ScheduleObserver(Recorder()).update({})

    second = Recorder()
    ScheduleObserver(second).update({})

    assert second.commands == []


# =============================================================================
# 4. Dữ liệu hỏng không được làm sập vòng cảm biến
# =============================================================================

def test_a_corrupt_row_is_skipped_without_raising(isolated_db) -> None:
    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "INSERT INTO schedule (run_at, json_cmd) VALUES "
            "(datetime('now','localtime','-1 second'), 'not json')"
        )

    recorder = Recorder()
    ScheduleObserver(recorder).update({})

    assert recorder.commands == []


def test_a_row_missing_a_device_is_skipped(isolated_db) -> None:
    with sqlite3.connect(str(isolated_db)) as conn:
        conn.execute(
            "INSERT INTO schedule (run_at, json_cmd) VALUES "
            "(datetime('now','localtime','-1 second'), ?)",
            (json.dumps({"action": "turn_on", "room": "bedroom"}),),
        )

    recorder = Recorder()
    ScheduleObserver(recorder).update({})

    assert recorder.commands == []


def test_a_failing_reader_does_not_break_the_sensor_loop() -> None:
    """
    ScheduleObserver treo giữa vòng poll sẽ kéo theo cả rule engine.
    Subject.notify() đã cô lập lỗi từng observer, nhưng lớp này cũng tự chịu.
    """
    def broken():
        raise sqlite3.OperationalError("database is locked")

    recorder = Recorder()
    observer = ScheduleObserver(recorder, schedule_reader=broken)

    observer.update({})

    assert recorder.commands == []