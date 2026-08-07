"""
Test: điều kiện và độ trễ phải SỐNG SÓT qua một lượt hỏi lại.

CHẠY OFFLINE.

LỖI ĐƯỢC KHOÁ LẠI Ở ĐÂY, quan sát trên console với Gemini thật:

    User: sau 5 phút nếu nhiệt độ trên 30 thì bật quạt
    Bot : Bạn muốn bật quạt ở phòng nào? Hiện có quạt ở: phòng ngủ, phòng khách.
    User: phòng ngủ
    Bot : Đã bật quạt phòng ngủ.

Người dùng yêu cầu một quy tắc có hẹn giờ và nhận về một lệnh chạy NGAY. Cả
điều kiện lẫn độ trễ biến mất, câu trả lời nghe hoàn toàn bình thường, và không
có lỗi nào trong log - cùng mẫu với những lỗi âm thầm khác trong hệ thống.

Hai nguyên nhân, cần chặn cả hai:
  1. merge_with_pending() chỉ mang SLOT_FIELDS sang; delay_seconds và
     unsupported không nằm trong đó.
  2. Model tự đặt condition=null khi nó quyết định hỏi lại - việc đó prompt
     phải dạy, và test này không kiểm được. Nó chỉ đảm bảo rằng NẾU model giữ
     lại thì server không đánh rơi.
"""

from __future__ import annotations

from modules.llm_integration.llm_module import (
    make_clarify_command,
    merge_with_pending,
)


CONDITION = {"sensor": "temperature", "operator": ">", "value": 30}

ANSWER = {
    "intent": "clarify",
    "action": None,
    "device": None,
    "room": "bedroom",
    "condition": None,
    "face_auth": False,
    "response": "",
}


def pending(**extra):
    base = {
        "intent": "clarify",
        "action": "turn_on",
        "device": "fan",
        "room": None,
        "condition": None,
        "face_auth": False,
        "response": "Bạn muốn bật quạt ở phòng nào?",
    }
    base.update(extra)
    return base


# =============================================================================
# 1. Điều kiện sống sót
# =============================================================================

def test_a_condition_survives_a_clarification_turn() -> None:
    merged = merge_with_pending(dict(ANSWER), pending(condition=CONDITION))

    assert merged["condition"] == CONDITION


def test_the_merged_command_becomes_a_rule_not_an_immediate_action() -> None:
    """Điểm mấu chốt: execute nghĩa là quạt bật ngay, sai ý người dùng."""
    merged = merge_with_pending(dict(ANSWER), pending(condition=CONDITION))

    assert merged["intent"] == "create_rule"


# =============================================================================
# 2. Độ trễ sống sót
# =============================================================================

def test_a_delay_survives_a_clarification_turn() -> None:
    merged = merge_with_pending(dict(ANSWER), pending(delay_seconds=300))

    assert merged["delay_seconds"] == 300


def test_a_delayed_command_becomes_a_schedule() -> None:
    merged = merge_with_pending(dict(ANSWER), pending(delay_seconds=300))

    assert merged["intent"] == "schedule"


# =============================================================================
# 3. Điều kiện CỘNG độ trễ vẫn là thứ chưa hỗ trợ
# =============================================================================

def test_a_condition_plus_a_delay_is_flagged_unsupported() -> None:
    """
    Một quy tắc có giờ khởi động. Hệ thống chưa làm được, nhưng cũng KHÔNG
    được im lặng bỏ phần trễ - phải hỏi người dùng.
    """
    merged = merge_with_pending(
        dict(ANSWER), pending(condition=CONDITION, delay_seconds=300)
    )

    assert merged["intent"] == "create_rule"
    assert merged["condition"] == CONDITION
    assert merged["unsupported"]


def test_an_existing_unsupported_note_is_not_overwritten() -> None:
    merged = merge_with_pending(
        dict(ANSWER),
        pending(condition=CONDITION, delay_seconds=300, unsupported="lặp mỗi ngày"),
    )

    assert merged["unsupported"] == "lặp mỗi ngày"


# =============================================================================
# 4. Lệnh thường không bị ảnh hưởng
# =============================================================================

def test_a_plain_command_is_unaffected() -> None:
    merged = merge_with_pending(
        dict(ANSWER), pending(action="turn_on", device="light")
    )

    assert merged["intent"] == "control_device"
    assert merged.get("delay_seconds") is None
    assert merged.get("condition") is None


def test_a_status_query_is_unaffected() -> None:
    merged = merge_with_pending(
        dict(ANSWER), pending(action="get_status", device="fan")
    )

    assert merged["intent"] == "query_status"


def test_the_newer_utterance_still_wins_over_inherited_context() -> None:
    """Người dùng được quyền đổi ý: giá trị vừa nói thắng giá trị kế thừa."""
    answer = {**ANSWER, "delay_seconds": 60}

    merged = merge_with_pending(answer, pending(delay_seconds=300))

    assert merged["delay_seconds"] == 60


# =============================================================================
# 5. Chỗ thực sự đánh rơi ngữ cảnh: server dựng lại clarify command
#
# merge_with_pending() luôn đúng - nó chỉ không có gì để mang sang, vì command
# đã bị thay thế TRƯỚC khi được lưu. Server dựng lại clarify command để tự soạn
# câu hỏi (nguyên tắc "server quyết định tập lựa chọn"), và bản trước chỉ mang
# action/device/room. Bốn test dưới đây khoá lại đúng bước đó.
# =============================================================================

def test_the_rebuilt_clarify_command_keeps_the_condition() -> None:
    rebuilt = make_clarify_command(
        response="Bạn muốn bật quạt ở phòng nào?",
        action="turn_on",
        device="fan",
        room=None,
        condition=CONDITION,
    )

    assert rebuilt["condition"] == CONDITION


def test_the_rebuilt_clarify_command_keeps_the_delay() -> None:
    rebuilt = make_clarify_command(
        response="...", action="turn_on", device="fan", room=None,
        delay_seconds=300,
    )

    assert rebuilt["delay_seconds"] == 300


def test_the_rebuilt_clarify_command_keeps_the_unsupported_note() -> None:
    rebuilt = make_clarify_command(
        response="...", action="turn_on", device="fan", room=None,
        unsupported="hẹn giờ 5 phút",
    )

    assert rebuilt["unsupported"] == "hẹn giờ 5 phút"


def test_the_whole_two_turn_path_preserves_the_request() -> None:
    """
    Ca đầy đủ quan sát được trên console, hai bước ghép lại.

    Lượt 1: model hiểu đúng -> server dựng lại clarify command -> lưu pending
    Lượt 2: người dùng trả lời phòng -> ghép -> phải ra một quy tắc, KHÔNG phải
            một lệnh chạy ngay.
    """
    pending_command = make_clarify_command(
        response="Bạn muốn bật quạt ở phòng nào?",
        action="turn_on",
        device="fan",
        room=None,
        condition=CONDITION,
        delay_seconds=300,
        unsupported="hẹn giờ 5 phút",
    )

    merged = merge_with_pending(dict(ANSWER), pending_command)

    assert merged["intent"] == "create_rule"
    assert merged["condition"] == CONDITION
    assert merged["unsupported"]


def test_a_plain_clarify_is_unchanged() -> None:
    """Không truyền gì thêm thì hành vi y như trước."""
    rebuilt = make_clarify_command(response="Bạn muốn bật thiết bị nào?")

    assert rebuilt["condition"] is None
    assert rebuilt["delay_seconds"] is None
    assert rebuilt["unsupported"] is None