"""
Tests cho query_status (P1-4).

Trước: "quạt phòng ngủ đang thế nào" -> hệ thống trả
       "Đang kiểm tra trạng thái thiết bị." rồi im lặng. Người dùng
       không bao giờ biết cái quạt đang bật hay tắt.

Sau:   trạng thái thật từ phần cứng được đưa ngược lên câu trả lời:
       "Quạt ở phòng ngủ đang bật."
"""

from __future__ import annotations

import io
import contextlib
from typing import Any

import pytest

from modules.llm_integration.llm_module import state_display_name
from services.command_service import CommandService, MockHardwareModule
from system_core.commands import GetStatusCommand, create_device_command


def quiet(func, *args, **kwargs):
    """Chạy hàm và nuốt stdout của MockHardware."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


# =============================================================================
# 1. Từ điển trạng thái lấy từ config
# =============================================================================

@pytest.mark.parametrize(
    "state, expected",
    [
        ("on", "đang bật"),
        ("off", "đang tắt"),
        ("open", "đang mở"),
        ("closed", "đang đóng"),
    ],
)
def test_state_display_name_from_config(state: str, expected: str):
    assert state_display_name(state) == expected


def test_unknown_state_does_not_crash():
    assert "không xác định" in state_display_name(None)
    assert "không xác định" in state_display_name("blah")


# =============================================================================
# 2. Command giữ lại payload của phần cứng
# =============================================================================

class StubHardware:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
        return self.result


def test_get_status_command_exposes_state():
    hardware = StubHardware({"status": "success", "state": "on"})
    command = create_device_command(
        command_data={"device": "fan", "action": "get_status", "room": "bedroom"},
        hardware=hardware,
    )

    assert isinstance(command, GetStatusCommand)
    assert command.execute() is True
    assert command.state == "on"


def test_command_keeps_last_result_for_control_actions():
    hardware = StubHardware({"status": "success", "state": "on"})
    command = create_device_command(
        command_data={"device": "light", "action": "turn_on", "room": "living_room"},
        hardware=hardware,
    )

    assert command.execute() is True
    assert command.last_result["state"] == "on"


def test_get_status_state_is_none_when_hardware_omits_it():
    """Phần cứng quên trả 'state' -> state=None, không được crash."""
    hardware = StubHardware({"status": "success"})
    command = create_device_command(
        command_data={"device": "fan", "action": "get_status", "room": "bedroom"},
        hardware=hardware,
    )

    assert command.execute() is True
    assert command.state is None


# =============================================================================
# 3. Mock hardware có trí nhớ
# =============================================================================

def test_mock_hardware_remembers_state_after_control():
    hardware = MockHardwareModule()

    quiet(
        hardware.execute_command,
        {"device": "light", "action": "turn_on", "room": "living_room"},
    )
    result = quiet(
        hardware.execute_command,
        {"device": "light", "action": "get_status", "room": "living_room"},
    )

    assert result["state"] == "on"


def test_mock_hardware_defaults_door_to_closed():
    hardware = MockHardwareModule()

    result = quiet(
        hardware.execute_command,
        {"device": "door", "action": "get_status", "room": "main_door"},
    )

    assert result["state"] == "closed"


def test_mock_hardware_tracks_rooms_separately():
    """Đèn phòng khách bật KHÔNG làm đèn phòng ngủ bật theo."""
    hardware = MockHardwareModule()

    quiet(
        hardware.execute_command,
        {"device": "light", "action": "turn_on", "room": "living_room"},
    )

    bedroom = quiet(
        hardware.execute_command,
        {"device": "light", "action": "get_status", "room": "bedroom"},
    )

    assert bedroom["state"] == "off"


# =============================================================================
# 4. End-to-end: câu trả lời phản ánh trạng thái thật
# =============================================================================

def test_query_status_reports_real_state_after_turning_on():
    service = CommandService(use_mock=True)

    quiet(service.handle_transcript, "bật quạt phòng ngủ")
    result = quiet(service.handle_transcript, "quạt phòng ngủ đang thế nào")

    assert result["next_step"] == "execute"
    assert result["response"] == "Quạt ở phòng ngủ đang bật."


def test_query_status_reflects_state_change():
    """Bật rồi tắt -> câu trả lời phải đổi theo."""
    service = CommandService(use_mock=True)

    quiet(service.handle_transcript, "bật đèn phòng khách")
    on = quiet(service.handle_transcript, "trạng thái đèn phòng khách")

    quiet(service.handle_transcript, "tắt đèn phòng khách")
    off = quiet(service.handle_transcript, "trạng thái đèn phòng khách")

    assert "đang bật" in on["response"]
    assert "đang tắt" in off["response"]


def test_query_status_no_longer_returns_placeholder():
    """
    Hồi quy: câu trả lời KHÔNG được là placeholder vô nghĩa nữa.
    """
    service = CommandService(use_mock=True)
    result = quiet(service.handle_transcript, "quạt phòng ngủ đang thế nào")

    assert "Đang kiểm tra trạng thái thiết bị." not in result["response"]
    assert "đang tắt" in result["response"]


def test_query_status_survives_hardware_without_state():
    """
    Phần cứng không trả 'state' -> vẫn phải trả lời tử tế,
    không được crash và không được rò None ra người dùng.
    """

    class StatelessHardware:
        def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
            return {"status": "success"}

    service = CommandService(use_mock=True, hardware_module=StatelessHardware())
    result = quiet(service.handle_transcript, "quạt phòng ngủ đang thế nào")

    assert result["ok"] is True
    assert "None" not in result["response"]
    assert "không xác định" in result["response"]


def test_status_avoids_redundant_room_when_names_collide():
    """
    device "door" và room "main_door" cùng hiển thị là "cửa chính".
    Không được ra câu "Cửa chính ở cửa chính đang đóng."
    """
    service = CommandService(use_mock=True)
    result = quiet(service.handle_transcript, "cửa chính đang thế nào")

    assert result["response"] == "Cửa chính đang đóng."