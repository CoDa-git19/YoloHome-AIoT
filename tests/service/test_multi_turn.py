"""
Tests cho hội thoại nhiều lượt / slot filling (P1-3).

Bug đã sửa: clarify là ngõ cụt.

    User : bật đèn
    Bot  : Bạn muốn điều khiển đèn ở phòng nào?
    User : phòng khách
    Bot  : Bạn muốn điều khiển thiết bị nào?     <- quên sạch, hỏi lại từ đầu

Bot hỏi nhưng không nghe câu trả lời -> vòng lặp vô tận.
"""

from __future__ import annotations

import contextlib
import io
import time
from typing import Any

import pytest

from modules.llm_integration.llm_module import (
    is_topic_change,
    merge_with_pending,
    parse_and_validate,
)
from services.command_service import CommandService
from services.session_service import SessionService


def quiet(func, *args, **kwargs):
    """Chạy hàm và nuốt stdout của MockHardware."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


class RecordingHardware:
    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
        self.commands.append(dict(command))
        return {"status": "success", "state": "on"}


@pytest.fixture
def service() -> CommandService:
    return CommandService(use_mock=True, session_service=SessionService())


# =============================================================================
# 1. Ghép slot
# =============================================================================

def test_merge_fills_missing_slots_from_pending():
    pending = {
        "intent": "clarify",
        "action": "turn_on",
        "device": "light",
        "room": None,
        "condition": None,
        "face_auth": False,
        "response": "Ở phòng nào?",
    }
    reply = {
        "intent": "clarify",
        "action": None,
        "device": None,
        "room": "living_room",
        "condition": None,
        "face_auth": False,
        "response": "Thiết bị nào?",
    }

    merged = merge_with_pending(reply, pending)

    assert merged["intent"] == "control_device"
    assert merged["action"] == "turn_on"
    assert merged["device"] == "light"
    assert merged["room"] == "living_room"


def test_merge_lets_new_utterance_override_old_slot():
    """User được quyền đổi ý: slot mới ghi đè slot cũ."""
    pending = {
        "intent": "clarify",
        "action": "turn_on",
        "device": "light",
        "room": None,
        "condition": None,
        "face_auth": False,
        "response": "",
    }
    reply = {
        "intent": "clarify",
        "action": "turn_off",
        "device": None,
        "room": "bedroom",
        "condition": None,
        "face_auth": False,
        "response": "",
    }

    merged = merge_with_pending(reply, pending)

    assert merged["action"] == "turn_off"


def test_merge_without_pending_is_noop():
    command = {"intent": "clarify", "action": None, "device": None, "room": None}
    assert merge_with_pending(command, None) == command


def test_complete_command_is_a_topic_change():
    assert is_topic_change(
        {"intent": "control_device", "action": "turn_off", "device": "fan", "room": "bedroom"}
    )


def test_incomplete_command_is_not_a_topic_change():
    assert not is_topic_change(
        {"intent": "clarify", "action": None, "device": None, "room": "living_room"}
    )


# =============================================================================
# 2. Hội thoại end-to-end
# =============================================================================

def test_missing_room_is_filled_by_next_turn(service: CommandService):
    first = quiet(service.handle_transcript, "bật đèn", session_id="s1")

    assert first["next_step"] == "clarify"
    assert first["awaiting_reply"] is True
    assert "phòng nào" in first["response"]

    second = quiet(service.handle_transcript, "phòng khách", session_id="s1")

    assert second["next_step"] == "execute"
    assert second["awaiting_reply"] is False
    assert second["command"]["device"] == "light"
    assert second["command"]["room"] == "living_room"
    assert second["command"]["action"] == "turn_on"


def test_missing_action_is_filled_by_next_turn(service: CommandService):
    """
    "bật lên" một mình không có device, nên parser không biết đó là turn_on
    hay open. Phải dùng device từ lượt trước làm gợi ý.
    """
    quiet(service.handle_transcript, "đèn phòng khách", session_id="s2")
    second = quiet(service.handle_transcript, "bật lên", session_id="s2")

    assert second["next_step"] == "execute"
    assert second["command"]["action"] == "turn_on"


def test_slots_accumulate_across_three_turns(service: CommandService):
    quiet(service.handle_transcript, "bật cái đó lên", session_id="s3")
    quiet(service.handle_transcript, "quạt", session_id="s3")
    third = quiet(service.handle_transcript, "phòng ngủ", session_id="s3")

    # Đã biết quạt + phòng ngủ, chỉ còn thiếu hành động.
    assert third["command"]["device"] == "fan"
    assert third["command"]["room"] == "bedroom"

    fourth = quiet(service.handle_transcript, "bật", session_id="s3")
    assert fourth["next_step"] == "execute"
    assert fourth["command"]["action"] == "turn_on"


def test_question_is_regenerated_with_known_slots(service: CommandService):
    """
    Sau khi biết là quạt, KHÔNG được hỏi lại "bạn muốn điều khiển thiết bị nào?".
    """
    quiet(service.handle_transcript, "bật cái đó lên", session_id="s4")
    second = quiet(service.handle_transcript, "quạt", session_id="s4")

    assert "quạt" in second["response"]
    assert "phòng nào" in second["response"]


def test_user_can_change_topic_mid_clarification(service: CommandService):
    """Đang hỏi về đèn, user quay sang lệnh khác -> bỏ câu hỏi cũ."""
    quiet(service.handle_transcript, "bật đèn", session_id="s5")
    second = quiet(service.handle_transcript, "tắt quạt phòng ngủ", session_id="s5")

    assert second["next_step"] == "execute"
    assert second["command"]["device"] == "fan"
    assert second["command"]["action"] == "turn_off"


def test_session_is_cleared_after_command_completes(service: CommandService):
    quiet(service.handle_transcript, "bật đèn", session_id="s6")
    quiet(service.handle_transcript, "phòng khách", session_id="s6")

    assert service.session_service.get_pending("s6") is None


def test_sessions_do_not_leak_into_each_other(service: CommandService):
    quiet(service.handle_transcript, "bật đèn", session_id="alice")
    quiet(service.handle_transcript, "tắt quạt", session_id="bob")

    result = quiet(service.handle_transcript, "phòng khách", session_id="alice")

    assert result["command"]["device"] == "light"
    assert result["command"]["action"] == "turn_on"


def test_without_session_id_behaviour_is_unchanged(service: CommandService):
    """Không truyền session_id -> mỗi câu là một lệnh độc lập (hành vi cũ)."""
    quiet(service.handle_transcript, "bật đèn")
    second = quiet(service.handle_transcript, "phòng khách")

    assert second["next_step"] == "clarify"


# =============================================================================
# 3. Chặn vòng lặp clarify vô tận
# =============================================================================

def test_clarify_loop_is_capped(service: CommandService):
    """User trả lời vô nghĩa mãi -> hệ thống phải dừng, không hỏi vô tận."""
    quiet(service.handle_transcript, "bật đèn", session_id="s7")

    for filler in ["ờ", "ừm"]:
        quiet(service.handle_transcript, filler, session_id="s7")

    final = quiet(service.handle_transcript, "hửm", session_id="s7")

    assert final["next_step"] == "stop"
    assert final["awaiting_reply"] is False
    assert final["result"] == "fail: clarify_limit"
    assert service.session_service.get_pending("s7") is None


# =============================================================================
# 4. TTL
# =============================================================================

def test_pending_command_expires():
    """Nửa tiếng sau, "phòng khách" là câu nói mới - không phải trả lời câu cũ."""
    sessions = SessionService(ttl_seconds=0.05)
    service = CommandService(use_mock=True, session_service=sessions)

    quiet(service.handle_transcript, "bật đèn", session_id="s8")
    time.sleep(0.1)

    assert sessions.get_pending("s8") is None

    result = quiet(service.handle_transcript, "phòng khách", session_id="s8")
    assert result["next_step"] == "clarify"


def test_ttl_zero_disables_expiry():
    sessions = SessionService(ttl_seconds=0.0)
    sessions.set_pending("s9", {"intent": "clarify", "device": "light"})

    time.sleep(0.05)
    assert sessions.get_pending("s9") is not None


# =============================================================================
# 5. An toàn: multi-turn KHÔNG được lách Face Auth
# =============================================================================

def test_multi_turn_cannot_bypass_face_auth():
    """
    Ghép slot qua nhiều lượt để tới lệnh mở cửa.
    Cửa vẫn TUYỆT ĐỐI không được mở nếu chưa xác thực.
    """
    hardware = RecordingHardware()
    service = CommandService(
        use_mock=True,
        hardware_module=hardware,
        session_service=SessionService(),
    )

    quiet(service.handle_transcript, "mở cái đó", session_id="attack")
    quiet(service.handle_transcript, "cửa chính", session_id="attack")
    result = quiet(service.handle_transcript, "mở", session_id="attack")

    assert hardware.commands == [], "Cửa đã bị mở qua multi-turn!"
    assert result["next_step"] != "execute"


def test_door_open_via_slot_filling_still_requires_auth():
    hardware = RecordingHardware()
    service = CommandService(
        use_mock=True,
        hardware_module=hardware,
        session_service=SessionService(),
    )

    quiet(service.handle_transcript, "mở cửa chính", session_id="s10")

    assert hardware.commands == []


# =============================================================================
# 6. pending_command đi tới tận LLM
# =============================================================================

def test_pending_command_reaches_parse_and_validate():
    pending = {
        "intent": "clarify",
        "action": "turn_on",
        "device": "light",
        "room": None,
        "condition": None,
        "face_auth": False,
        "response": "",
    }

    result = parse_and_validate(
        "phòng khách",
        pending_command=pending,
        use_mock=True,
    )

    assert result["next_step"] == "execute"
    assert result["command"]["room"] == "living_room"

def test_reject_giu_pending_khi_dang_clarify():
    svc = CommandService(use_mock=True)
    sid = "t1"

    r1 = svc.handle_transcript("bật quạt", session_id=sid)
    assert r1["next_step"] == "clarify"

    r2 = svc.handle_transcript("phòng bếp", session_id=sid)
    assert r2["next_step"] in {"reject", "registry_request"}
    assert svc.session_service.get_pending(sid) is not None   # <- lỗi cũ

    r3 = svc.handle_transcript("phòng khách", session_id=sid)
    assert r3["next_step"] == "execute"
    assert r3["command"]["device"] == "fan"                   # nhớ được "quạt"