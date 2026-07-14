"""
Tests cho Observer Pattern.

Trước đây pattern này CHẾT: Subject/Observer có sẵn nhưng notify() không được
gọi từ đâu cả. Hệ quả nghiêm trọng là RuleService.evaluate_sensor_data() cũng
không ai gọi - automation rule nằm im trong database và cái quạt không bao giờ
tự bật.

RuleObserver chính là mắt xích còn thiếu:

    HardwareModule.poll_sensors()
        -> notify(sensor_data)
            -> RuleObserver.update()
                -> RuleService.evaluate_sensor_data()
                    -> CommandService.execute_authorized_command()
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from typing import Any

import pytest

from config.settings import DB_PATH
from database.init_db import init_db
from services.command_service import CommandService, MockHardwareModule
from services.rule_service import RuleService
from system_core.observers import (
    AdafruitPublisher,
    Observer,
    RuleObserver,
    SensorLoggingObserver,
    Subject,
)


def quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


# =============================================================================
# Test doubles
# =============================================================================

class SpyObserver(Observer):
    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def update(self, sensor_data: dict[str, Any]) -> None:
        self.seen.append(dict(sensor_data))


class BrokenObserver(Observer):
    """Mô phỏng Adafruit IO sập."""

    def update(self, sensor_data: dict[str, Any]) -> None:
        raise RuntimeError("Adafruit IO không phản hồi")


class SensorSource(Subject):
    pass


class RecordingExecutor:
    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def execute_authorized_command(self, command: dict[str, Any]) -> bool:
        self.commands.append(dict(command))
        return True


class StubRuleEngine:
    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = actions
        self.calls: list[dict[str, Any]] = []

    def evaluate_sensor_data(self, current_sensor_data):
        self.calls.append(dict(current_sensor_data))
        return self.actions


# =============================================================================
# 1. Subject: attach / detach / notify
# =============================================================================

def test_attached_observer_receives_sensor_data():
    source = SensorSource()
    spy = SpyObserver()
    source.attach(spy)

    source.notify({"temperature": 31.0})

    assert spy.seen == [{"temperature": 31.0}]


def test_all_observers_receive_the_same_data():
    source = SensorSource()
    first, second = SpyObserver(), SpyObserver()
    source.attach(first)
    source.attach(second)

    source.notify({"humidity": 65})

    assert first.seen == second.seen == [{"humidity": 65}]


def test_attaching_twice_does_not_duplicate():
    source = SensorSource()
    spy = SpyObserver()

    source.attach(spy)
    source.attach(spy)
    source.notify({"temperature": 30})

    assert len(spy.seen) == 1
    assert len(source.observers) == 1


def test_detached_observer_stops_receiving():
    source = SensorSource()
    spy = SpyObserver()

    source.attach(spy)
    source.detach(spy)
    source.notify({"temperature": 30})

    assert spy.seen == []


def test_detaching_unknown_observer_does_not_raise():
    """list.remove() ném ValueError. Gỡ observer không tồn tại phải im lặng."""
    source = SensorSource()

    source.detach(SpyObserver())  # không được nổ


def test_notify_with_no_observers_is_safe():
    assert SensorSource().notify({"temperature": 30}) == []


# =============================================================================
# 2. Cô lập lỗi: một observer hỏng KHÔNG được giết cả dây chuyền
# =============================================================================

def test_broken_observer_does_not_block_the_others():
    """
    Adafruit sập thì rule engine VẪN phải chạy, nếu không cái quạt sẽ
    không bao giờ tự bật.
    """
    source = SensorSource()
    spy = SpyObserver()

    source.attach(BrokenObserver())
    source.attach(spy)

    errors = source.notify({"temperature": 35.0})

    assert spy.seen == [{"temperature": 35.0}]
    assert len(errors) == 1


def test_notify_does_not_propagate_observer_errors():
    source = SensorSource()
    source.attach(BrokenObserver())

    errors = source.notify({"temperature": 35.0})  # không được ném ra ngoài

    assert isinstance(errors[0], RuntimeError)


def test_observer_order_does_not_matter_for_isolation():
    source = SensorSource()
    spy = SpyObserver()

    source.attach(spy)
    source.attach(BrokenObserver())

    source.notify({"temperature": 35.0})

    assert spy.seen != []


# =============================================================================
# 3. RuleObserver: mắt xích còn thiếu
# =============================================================================

def test_rule_observer_forwards_sensor_data_to_rule_engine():
    engine = StubRuleEngine(actions=[])
    observer = RuleObserver(engine, RecordingExecutor())

    observer.update({"temperature": 31.0})

    assert engine.calls == [{"temperature": 31.0}]


def test_rule_observer_executes_triggered_actions():
    engine = StubRuleEngine(
        actions=[
            {
                "rule_id": 1,
                "action": "turn_on",
                "device": "fan",
                "room": "living_room",
                "response": "Bật quạt.",
            }
        ]
    )
    executor = RecordingExecutor()
    observer = RuleObserver(engine, executor)

    observer.update({"temperature": 31.0})

    assert len(executor.commands) == 1
    assert executor.commands[0]["device"] == "fan"
    assert executor.commands[0]["action"] == "turn_on"
    assert observer.triggered_count == 1


def test_rule_observer_does_nothing_when_no_rule_fires():
    executor = RecordingExecutor()
    observer = RuleObserver(StubRuleEngine(actions=[]), executor)

    observer.update({"temperature": 20.0})

    assert executor.commands == []


def test_rule_observer_refuses_actions_requiring_face_auth():
    """
    Phòng thủ nhiều lớp: rule mở cửa lẽ ra đã bị chặn từ lúc tạo. Nhưng nếu
    có ai lách được vào database, đây là chốt chặn cuối - rule chạy tự động
    thì không có ai đứng trước camera.
    """
    engine = StubRuleEngine(
        actions=[
            {
                "rule_id": 99,
                "action": "open",
                "device": "door",
                "room": "main_door",
                "response": "Mở cửa.",
            }
        ]
    )
    executor = RecordingExecutor()
    observer = RuleObserver(engine, executor)

    observer.update({"temperature": 31.0})

    assert executor.commands == [], "Rule đã mở cửa mà không cần Face Auth!"
    assert observer.triggered_count == 0


# =============================================================================
# 4. SensorLoggingObserver
# =============================================================================

def test_sensor_logging_observer_keeps_history():
    observer = SensorLoggingObserver()

    observer.update({"temperature": 30})
    observer.update({"temperature": 31})

    assert len(observer.history) == 2
    assert observer.latest == {"temperature": 31}


def test_sensor_history_is_bounded():
    """Chạy 24/7 mà không giới hạn thì lịch sử phình vô hạn."""
    observer = SensorLoggingObserver(max_history=3)

    for value in range(10):
        observer.update({"temperature": value})

    assert len(observer.history) == 3
    assert observer.latest == {"temperature": 9}


def test_sensor_logging_observer_copies_data():
    """Observer không được giữ tham chiếu tới dict mà caller còn sửa."""
    observer = SensorLoggingObserver()
    payload = {"temperature": 30}

    observer.update(payload)
    payload["temperature"] = 99

    assert observer.latest == {"temperature": 30}


# =============================================================================
# 5. AdafruitPublisher
# =============================================================================

def test_adafruit_publisher_forwards_to_hardware():
    class FakeHardware:
        def __init__(self):
            self.published = []

        def publish_to_adafruit(self, data):
            self.published.append(data)

    hardware = FakeHardware()
    AdafruitPublisher(hardware).update({"temperature": 30})

    assert hardware.published == [{"temperature": 30}]


# =============================================================================
# 6. End-to-end: cảm biến -> rule -> phần cứng
# =============================================================================

@pytest.fixture
def wired():
    init_db()

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("DELETE FROM automation_rules")
        conn.commit()

    hardware = MockHardwareModule()
    rules = RuleService(hysteresis=0.5)
    service = CommandService(
        hardware_module=hardware,
        rule_service=rules,
        use_mock=True,
    )

    observer = RuleObserver(rules, service)
    hardware.attach(observer)

    return hardware, rules, service, observer


def test_sensor_reading_triggers_automation_rule(wired):
    """
    Test quan trọng nhất của file này.

    Nói ra luật -> cảm biến nóng lên -> quạt TỰ BẬT.
    Trước khi có RuleObserver, chuỗi này đứt và quạt không bao giờ bật.
    """
    hardware, _, service, _ = wired

    quiet(
        service.handle_transcript,
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách",
    )

    hardware.sensor_data["temperature"] = 32.0
    quiet(hardware.poll_sensors)

    assert hardware._states.get(("living_room", "fan")) == "on"


def test_rule_does_not_fire_below_threshold(wired):
    hardware, _, service, _ = wired

    quiet(
        service.handle_transcript,
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách",
    )

    hardware.sensor_data["temperature"] = 25.0
    quiet(hardware.poll_sensors)

    assert ("living_room", "fan") not in hardware._states


def test_rule_stays_edge_triggered_through_the_observer(wired):
    """
    P1-6 vẫn giữ nguyên khi đi qua Observer: nóng liên tục chỉ bật MỘT lần,
    không spam phần cứng mỗi vòng đọc cảm biến.
    """
    hardware, _, service, observer = wired

    quiet(
        service.handle_transcript,
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách",
    )

    hardware.sensor_data["temperature"] = 32.0
    for _ in range(5):
        quiet(hardware.poll_sensors)

    assert observer.triggered_count == 1


def test_multiple_observers_run_on_each_poll(wired):
    hardware, _, _, _ = wired
    logging_observer = SensorLoggingObserver()
    hardware.attach(logging_observer)

    quiet(hardware.poll_sensors)
    quiet(hardware.poll_sensors)

    assert len(logging_observer.history) == 2