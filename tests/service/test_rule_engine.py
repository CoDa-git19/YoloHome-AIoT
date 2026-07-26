"""
Tests cho Automation Rules Engine (P1-6).

Bug đã sửa: evaluate_sensor_data() trả về hành động ở MỌI vòng đọc cảm biến
khi điều kiện đang đúng. Rule "nhiệt độ > 30 thì bật quạt" gặp ngày nóng sẽ
gửi lệnh turn_on xuống Yolo:Bit vài giây một lần suốt cả ngày.

Cách sửa:
- Edge-triggered: chỉ kích hoạt khi điều kiện CHUYỂN sai -> đúng.
- Hysteresis: chỉ nạp lại rule khi giá trị ra khỏi ngưỡng một khoảng deadband.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from config.settings import DB_PATH
from database.init_db import init_db
from services.rule_service import RuleService


@pytest.fixture
def rules() -> RuleService:
    """RuleService với bảng automation_rules sạch."""
    init_db()

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM automation_rules")
        conn.commit()

    return RuleService(hysteresis=0.5)


def make_rule_command(
    *,
    sensor: str = "temperature",
    operator: str = ">",
    value: float = 30,
    action: str = "turn_on",
    device: str = "fan",
    room: str = "living_room",
) -> dict[str, Any]:
    return {
        "intent": "create_rule",
        "action": action,
        "device": device,
        "room": room,
        "face_auth": False,
        "condition": {"sensor": sensor, "operator": operator, "value": value},
        "response": "Đã tạo luật.",
    }


# =============================================================================
# 1. Edge-triggered: không spam phần cứng
# =============================================================================

def test_rule_fires_only_once_while_condition_stays_true(rules: RuleService):
    """
    Trời nóng 32 độ suốt 8 vòng đọc cảm biến.
    Quạt chỉ được bật MỘT lần, không phải 8 lần.
    """
    rules.create_rule(None, make_rule_command())

    fired = sum(
        len(rules.evaluate_sensor_data({"temperature": 32.0})) for _ in range(8)
    )

    assert fired == 1


def test_rule_does_not_fire_when_condition_false(rules: RuleService):
    rules.create_rule(None, make_rule_command())

    assert rules.evaluate_sensor_data({"temperature": 25.0}) == []


def test_rule_can_fire_again_after_condition_clearly_resets(rules: RuleService):
    """Nóng -> bật. Mát hẳn -> nạp lại. Nóng lại -> bật lần nữa."""
    rules.create_rule(None, make_rule_command())

    assert len(rules.evaluate_sensor_data({"temperature": 32.0})) == 1
    assert rules.evaluate_sensor_data({"temperature": 32.0}) == []

    # Xuống 25 độ: rõ ràng sai (dưới 30 - 0.5) -> nạp lại
    assert rules.evaluate_sensor_data({"temperature": 25.0}) == []

    assert len(rules.evaluate_sensor_data({"temperature": 32.0})) == 1


def test_triggered_action_carries_device_and_room(rules: RuleService):
    rules.create_rule(None, make_rule_command())

    actions = rules.evaluate_sensor_data({"temperature": 32.0})

    assert len(actions) == 1
    assert actions[0]["device"] == "fan"
    assert actions[0]["room"] == "living_room"
    assert actions[0]["action"] == "turn_on"


def test_missing_sensor_reading_is_ignored(rules: RuleService):
    rules.create_rule(None, make_rule_command())

    assert rules.evaluate_sensor_data({"humidity": 80}) == []


# =============================================================================
# 2. Hysteresis: chống bật/tắt xoành xoạch quanh ngưỡng
# =============================================================================

def test_hysteresis_blocks_flapping_around_threshold(rules: RuleService):
    """
    Nhiệt độ dao động quanh 30: 29.8 / 30.2 / 29.9 / 30.1 ...

    Không có hysteresis, quạt sẽ bật lại mỗi lần vượt ngưỡng.
    Với deadband 0.5, chỉ khi xuống dưới 29.5 rule mới được nạp lại.
    """
    rules.create_rule(None, make_rule_command())

    readings = [29.8, 30.2, 29.9, 30.1, 29.7, 30.3]
    fired = sum(
        len(rules.evaluate_sensor_data({"temperature": t})) for t in readings
    )

    assert fired == 1


def test_hysteresis_zero_allows_immediate_reset():
    """hysteresis=0 -> quay lại hành vi edge-trigger thuần."""
    init_db()

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM automation_rules")
        conn.commit()

    service = RuleService(hysteresis=0.0)
    service.create_rule(None, make_rule_command())

    readings = [30.2, 29.9, 30.1]
    fired = sum(
        len(service.evaluate_sensor_data({"temperature": t})) for t in readings
    )

    assert fired == 2


def test_hysteresis_applies_to_less_than_operator(rules: RuleService):
    """Rule "độ ẩm < 40": chỉ nạp lại khi độ ẩm lên trên 40.5."""
    rules.create_rule(
        None,
        make_rule_command(sensor="humidity", operator="<", value=40),
    )

    readings = [35.0, 40.2, 38.0]  # bật, ra khỏi ngưỡng nhưng chưa đủ xa, lại vào
    fired = sum(
        len(rules.evaluate_sensor_data({"humidity": h})) for h in readings
    )

    assert fired == 1

    # Lên 45 -> rõ ràng sai -> nạp lại
    rules.evaluate_sensor_data({"humidity": 45.0})
    assert len(rules.evaluate_sensor_data({"humidity": 35.0})) == 1


# =============================================================================
# 3. Chống rule trùng lặp
# =============================================================================

def test_duplicate_rule_returns_existing_id(rules: RuleService):
    """Nói 2 lần cùng một câu -> không tạo ra 2 rule giống hệt."""
    first = rules.create_rule(None, make_rule_command())
    second = rules.create_rule(None, make_rule_command())

    assert first == second
    assert len(rules.get_active_rules()) == 1


def test_different_rules_are_kept_separate(rules: RuleService):
    rules.create_rule(None, make_rule_command(room="living_room"))
    rules.create_rule(None, make_rule_command(room="bedroom"))

    assert len(rules.get_active_rules()) == 2


def test_deactivated_rule_stops_firing(rules: RuleService):
    rule_id = rules.create_rule(None, make_rule_command())
    rules.deactivate_rule(rule_id)

    assert rules.evaluate_sensor_data({"temperature": 32.0}) == []


# =============================================================================
# 4. Rule không hợp lệ
# =============================================================================

@pytest.mark.parametrize(
    "command",
    [
        {"action": "turn_on", "device": "fan", "room": "living_room"},  # thiếu condition
        {
            "action": "turn_on",
            "device": "fan",
            "room": "living_room",
            "condition": {"sensor": "temperature", "operator": ">"},  # thiếu value
        },
        {
            "action": "turn_on",
            "device": "fan",
            "condition": {"sensor": "temperature", "operator": ">", "value": 30},
        },  # thiếu room
    ],
)
def test_invalid_rule_is_rejected(rules: RuleService, command: dict[str, Any]):
    assert rules.create_rule(None, command) == -1
    assert rules.get_active_rules() == []


# =============================================================================
# 5. Trạng thái được lưu bền vào database
# =============================================================================

def test_rule_state_is_persisted(rules: RuleService):
    """
    last_state phải nằm trong DB, không phải bộ nhớ. Nếu không, restart
    hệ thống là rule lại kích hoạt lần nữa dù điều kiện không đổi.
    """
    rules.create_rule(None, make_rule_command())
    rules.evaluate_sensor_data({"temperature": 32.0})

    rule = rules.get_active_rules()[0]
    assert rule["last_state"] == 1
    assert rule["last_triggered_at"] is not None

    # Service mới (mô phỏng restart) -> vẫn không kích hoạt lại
    fresh = RuleService(hysteresis=0.5)
    assert fresh.evaluate_sensor_data({"temperature": 32.0}) == []