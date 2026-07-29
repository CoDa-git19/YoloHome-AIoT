"""
Test cho slot grounding (ground_slots).

CHẠY OFFLINE: không gọi Gemini.

QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
---------------------------
    Mọi giá trị room/device phải CÓ BẰNG CHỨNG trong câu người dùng vừa nói,
    hoặc kế thừa từ pending_command của lượt trước.

Lỗi gốc, quan sát được khi chạy console:

    Bot : Nhà bếp chưa được đăng ký. Bạn có muốn gửi yêu cầu thêm phòng này không?
    User: có
    Bot : Đã bật đèn phòng ngủ.          <-- phần cứng bị tác động thật

Chữ "có" không nhắc tới phòng nào. Model tự chọn "bedroom"; server thấy
bedroom + light là tổ hợp hợp lệ nên cho chạy. Đây là ca DUY NHẤT trong đợt
kiểm thử mà một giá trị model tự bịa đi được tới phần cứng.

Cùng nguyên tắc đã áp cho face_auth (enforce_policy) và cho ngưỡng khuôn mặt
(AuthService), lần này áp cho GIÁ TRỊ SLOT.
"""

from __future__ import annotations

import pytest

from modules.llm_integration.llm_module import (
    ground_slots,
    slot_has_evidence,
)


PENDING_LIGHT = {
    "intent": "control_device",
    "action": "turn_on",
    "device": "light",
    "room": None,
}


def command(device=None, room=None, action="turn_on") -> dict:
    return {
        "intent": "control_device",
        "action": action,
        "device": device,
        "room": room,
        "face_auth": False,
        "condition": None,
        "response": "",
    }


# =============================================================================
# 1. Ca gốc: câu trả lời không chứa thông tin
# =============================================================================

@pytest.mark.parametrize("answer", ["có", "ok", "ừ", "được", "vâng", "đồng ý"])
def test_affirmative_answer_never_fills_a_room(answer: str) -> None:
    """
    Câu đồng ý KHÔNG phải là tên phòng.

    Nếu test này đỏ, một chữ "có" có thể kích hoạt thiết bị ở một phòng bất kỳ.
    """
    result, dropped = ground_slots(
        command(device="light", room="bedroom"), answer, PENDING_LIGHT
    )

    assert result["room"] is None, f"{answer!r} đã điền được phòng."
    assert dropped, "Slot bị bỏ mà không ghi lại audit."


def test_hallucinated_device_is_dropped_too() -> None:
    result, dropped = ground_slots(command(device="fan", room="bedroom"), "ok", None)

    assert result["device"] is None
    assert result["room"] is None
    assert len(dropped) == 2


def test_dropped_slots_are_reported_for_audit() -> None:
    """
    Danh sách trả về được CommandService đẩy vào error_log.

    Không có nó thì việc model đi chệch không để lại dấu vết nào.
    """
    _, dropped = ground_slots(command(device="light", room="bedroom"), "có", PENDING_LIGHT)

    assert len(dropped) == 1
    assert "room" in dropped[0]
    assert "bedroom" in dropped[0]


# =============================================================================
# 2. Không được phá lệnh hợp lệ
# =============================================================================

def test_valid_answer_to_a_clarify_question_is_kept() -> None:
    result, dropped = ground_slots(
        command(device="light", room="living_room"), "phòng khách", PENDING_LIGHT
    )

    assert result["room"] == "living_room"
    assert result["device"] == "light"
    assert dropped == []


def test_full_single_turn_command_is_kept() -> None:
    result, dropped = ground_slots(
        command(device="light", room="living_room"), "bật đèn phòng khách", None
    )

    assert result == command(device="light", room="living_room")
    assert dropped == []


def test_short_alias_is_enough_evidence() -> None:
    """language_aliases cho phép "khách" là alias của living_room."""
    result, _ = ground_slots(
        command(device="light", room="living_room"), "ở khách nhé", PENDING_LIGHT
    )

    assert result["room"] == "living_room"


def test_registry_key_in_the_transcript_counts_as_evidence() -> None:
    """Người dùng (hoặc dashboard) có thể gõ thẳng khoá registry."""
    result, _ = ground_slots(
        command(device="light", room="living_room"), "bật light ở living_room", None
    )

    assert result["room"] == "living_room"
    assert result["device"] == "light"


def test_value_inherited_from_pending_is_always_allowed() -> None:
    """
    Slot kế thừa từ lượt trước KHÔNG cần bằng chứng ở lượt này - người dùng
    đã nói nó ở lượt trước rồi. Thiếu ngoại lệ này thì multi-turn chết hẳn.
    """
    pending = {"device": "light", "room": "living_room", "action": "turn_on"}

    result, dropped = ground_slots(
        command(device="light", room="living_room"), "bật", pending
    )

    assert result["device"] == "light"
    assert result["room"] == "living_room"
    assert dropped == []


# =============================================================================
# 3. Đầu vào lạ không được làm sập
# =============================================================================

@pytest.mark.parametrize("transcript", ["", "   ", "!!!", "123"])
def test_empty_or_junk_transcript_drops_everything(transcript: str) -> None:
    result, _ = ground_slots(command(device="light", room="bedroom"), transcript, None)

    assert result["room"] is None
    assert result["device"] is None


def test_none_values_are_left_alone() -> None:
    result, dropped = ground_slots(command(), "bật cái gì đó", None)

    assert result["device"] is None
    assert result["room"] is None
    assert dropped == []


def test_non_string_slot_value_does_not_crash() -> None:
    weird = command()
    weird["room"] = 123

    result, _ = ground_slots(weird, "bật đèn", None)

    assert result["room"] == 123  # để nguyên, validator sẽ loại sau


def test_other_fields_are_untouched() -> None:
    """Chỉ room và device bị soi. action, intent, condition giữ nguyên."""
    original = command(device="light", room="bedroom", action="turn_off")
    original["condition"] = {"sensor": "temperature", "operator": ">", "value": 30}

    result, _ = ground_slots(original, "có", PENDING_LIGHT)

    assert result["action"] == "turn_off"
    assert result["intent"] == "control_device"
    assert result["condition"] == original["condition"]


# =============================================================================
# 4. slot_has_evidence
# =============================================================================

@pytest.mark.parametrize(
    "value,text,expected",
    [
        ("living_room", "bật đèn phòng khách", True),
        ("living_room", "bật đèn phòng ngủ", False),
        ("bedroom", "phòng ngủ", True),
        ("main_door", "mở cửa chính", True),
        ("bedroom", "có", False),
    ],
)
def test_room_evidence(value: str, text: str, expected: bool) -> None:
    assert slot_has_evidence("room", value, text) is expected


@pytest.mark.parametrize(
    "value,text,expected",
    [
        ("light", "bật đèn", True),
        ("fan", "bật quạt", True),
        ("fan", "bật đèn", False),
        ("light", "ok", False),
    ],
)
def test_device_evidence(value: str, text: str, expected: bool) -> None:
    assert slot_has_evidence("device", value, text) is expected