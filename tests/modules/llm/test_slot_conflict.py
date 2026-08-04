"""
Test cho xử lý xung đột slot khi ghép ngữ cảnh (drop_inherited_conflict).

CHẠY OFFLINE: không gọi Gemini.

QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
---------------------------
    Khi ghép ngữ cảnh tạo ra tổ hợp (phòng, thiết bị) bất khả thi,
    GIÁ TRỊ VỪA NÓI THẮNG GIÁ TRỊ KẾ THỪA.

Lỗi gốc, quan sát được khi chạy console:

    User: cửa chính          -> pending: room=main_door
    User: bật đèn            -> ghép thành room=main_door + device=light
    Bot : Bạn muốn thực hiện hành động nào với đèn ở cửa chính?
    User: bật
    Bot : Đã bật đèn ở cửa chính.        <-- không có gì được bật

Cửa chính không có đèn. Hệ thống biết điều đó ngay lúc ghép, nhưng vẫn hỏi
thêm một câu vô nghĩa rồi mới trượt validation.

Ngữ cảnh cũ là bằng chứng YẾU HƠN câu người dùng vừa nói. Người vừa nói
"bật đèn" thì họ muốn đèn - cái cần hỏi lại là phòng.
"""

from __future__ import annotations

from modules.llm_integration.llm_module import (
    drop_inherited_conflict,
    missing_slot_question,
)


REGISTRY = {
    "living_room": {"light": ["turn_on", "turn_off"], "fan": ["turn_on", "turn_off"]},
    "bedroom": {"light": ["turn_on", "turn_off"]},
    "main_door": {"door": ["open", "close"]},
}


def resolve(merged: dict, fresh: dict, pending: dict | None):
    return drop_inherited_conflict(merged, fresh, pending, REGISTRY)


# =============================================================================
# 1. Ca gốc: phòng kế thừa bị bỏ
# =============================================================================

def test_inherited_room_is_dropped_when_it_lacks_the_device() -> None:
    result, notes = resolve(
        merged={"device": "light", "room": "main_door", "action": "turn_on"},
        fresh={"device": "light", "room": None, "action": "turn_on"},
        pending={"device": "door", "room": "main_door", "action": None},
    )

    assert result["device"] == "light", "Thiết bị vừa nói phải được giữ."
    assert result["room"] is None, "Phòng kế thừa phải bị bỏ."
    assert notes


def test_dropping_the_room_leads_to_a_useful_question() -> None:
    """Sau khi bỏ phòng, câu hỏi lại phải đúng và kèm gợi ý hợp lệ."""
    result, _ = resolve(
        merged={"device": "light", "room": "main_door", "action": "turn_on"},
        fresh={"device": "light", "room": None, "action": "turn_on"},
        pending={"device": "door", "room": "main_door", "action": None},
    )

    question = missing_slot_question(result, REGISTRY)

    assert "phòng nào" in question
    assert "phòng khách" in question
    assert "cửa chính" not in question


# =============================================================================
# 2. Chiều ngược lại: thiết bị kế thừa bị bỏ
# =============================================================================

def test_inherited_device_is_dropped_when_the_room_lacks_it() -> None:
    result, notes = resolve(
        merged={"device": "light", "room": "main_door", "action": "turn_on"},
        fresh={"device": None, "room": "main_door", "action": None},
        pending={"device": "light", "room": None, "action": "turn_on"},
    )

    assert result["room"] == "main_door", "Phòng vừa nói phải được giữ."
    assert result["device"] is None, "Thiết bị kế thừa phải bị bỏ."
    assert notes


# =============================================================================
# 3. Không được can thiệp quá tay
# =============================================================================

def test_valid_combination_is_left_alone() -> None:
    merged = {"device": "light", "room": "living_room", "action": "turn_on"}

    result, notes = resolve(
        merged=merged,
        fresh={"device": "light", "room": None, "action": "turn_on"},
        pending={"device": None, "room": "living_room", "action": None},
    )

    assert result == merged
    assert notes == []


def test_both_slots_from_the_current_utterance_are_left_for_the_validator() -> None:
    """
    "bật đèn cửa chính" nói một lượt là lệnh SAI THẬT, không phải lỗi ghép.

    Để nguyên cho validator từ chối kèm câu giải thích của failure_response(),
    thay vì âm thầm sửa thành một lệnh khác với ý người dùng.
    """
    merged = {"device": "light", "room": "main_door", "action": "turn_on"}

    result, notes = resolve(merged=merged, fresh=dict(merged), pending=None)

    assert result == merged
    assert notes == []


def test_missing_slot_is_not_a_conflict() -> None:
    merged = {"device": "light", "room": None, "action": "turn_on"}

    result, notes = resolve(merged=merged, fresh=dict(merged), pending=None)

    assert result == merged
    assert notes == []


def test_both_inherited_is_left_alone() -> None:
    """
    Cả hai cùng kế thừa nghĩa là tổ hợp hỏng đã tồn tại từ trước - không có
    "giá trị vừa nói" nào để ưu tiên. Để validator xử lý.
    """
    merged = {"device": "light", "room": "main_door", "action": "turn_on"}

    result, notes = resolve(
        merged=merged,
        fresh={"device": None, "room": None, "action": "turn_on"},
        pending={"device": "light", "room": "main_door", "action": None},
    )

    assert result == merged
    assert notes == []


def test_unknown_room_key_is_treated_as_a_conflict() -> None:
    """Phòng không có trong registry cũng là tổ hợp bất khả thi."""
    result, notes = resolve(
        merged={"device": "light", "room": "kitchen", "action": "turn_on"},
        fresh={"device": "light", "room": None, "action": "turn_on"},
        pending={"device": None, "room": "kitchen", "action": None},
    )

    assert result["device"] == "light"
    assert result["room"] is None
    assert notes


def test_notes_name_both_sides_of_the_conflict() -> None:
    """Audit phải đọc được: bỏ cái gì, vì cái gì."""
    _, notes = resolve(
        merged={"device": "fan", "room": "bedroom", "action": "turn_on"},
        fresh={"device": "fan", "room": None, "action": "turn_on"},
        pending={"device": None, "room": "bedroom", "action": None},
    )

    assert len(notes) == 1
    assert "bedroom" in notes[0]
    assert "fan" in notes[0]