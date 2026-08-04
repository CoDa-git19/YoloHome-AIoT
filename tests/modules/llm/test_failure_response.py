"""
Test cho câu trả lời khi lệnh KHÔNG chạy được (failure_response).

CHẠY OFFLINE: không gọi Gemini, không cần database.

QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
---------------------------
    Lệnh thất bại thì câu trả lời KHÔNG ĐƯỢC mang giọng thành công.

Lỗi gốc, quan sát được khi chạy console:

    Nhập lệnh: cửa chính
    Nhập lệnh: bật đèn
    Nhập lệnh: bật
    [Pipeline] next_step = stop
    [Bot]: Đã bật đèn ở cửa chính.        <-- không có gì được bật

Cửa chính không có đèn. Validation trượt, execution_status="failed" được ghi
vào command_log, nhưng người dùng nghe một câu khẳng định đã làm xong.

Nguyên nhân: command["response"] do LLM sinh ra TRƯỚC khi server validate,
và nhánh thất bại trong CommandService dùng lại nguyên câu đó.
"""

from __future__ import annotations

import pytest

from modules.llm_integration.llm_module import (
    device_in_room_text,
    failure_response,
    load_device_registry,
)


REGISTRY = {
    "living_room": {"light": ["turn_on", "turn_off"], "fan": ["turn_on", "turn_off"]},
    "bedroom": {"light": ["turn_on", "turn_off"]},
    "main_door": {"door": ["open", "close"]},
}

# Từ ngữ mang giọng "đã làm xong". Không được xuất hiện ở bất kỳ câu thất bại nào.
SUCCESS_WORDS = ["đã bật", "đã tắt", "đã mở", "đã đóng", "đã thực hiện", "đã xử lý"]


def fails(command: dict, code: str) -> str:
    return failure_response(command, {"code": code}, REGISTRY)


# =============================================================================
# 1. Bất biến: không bao giờ nghe như thành công
# =============================================================================

@pytest.mark.parametrize(
    "code",
    [
        "unknown_room",
        "unknown_device",
        "unsupported_action",
        "invalid_condition",
        "missing_field",
        "invalid_intent",
        "llm_parse_error",
        "llm_timeout",
        "llm_api_error",
        "llm_unavailable",
        "llm_rate_limited",
        "safety_rule_violation",
        "ma_loi_chua_biet",
    ],
)
def test_failure_never_sounds_like_success(code: str) -> None:
    text = fails(
        {"device": "light", "room": "main_door", "action": "turn_on"}, code
    ).lower()

    for word in SUCCESS_WORDS:
        assert word not in text, f"Mã {code!r} trả câu nghe như đã làm xong."


@pytest.mark.parametrize(
    "code",
    ["unknown_room", "unknown_device", "unsupported_action", "ma_loi_chua_biet"],
)
def test_failure_response_is_never_empty(code: str) -> None:
    text = fails({"device": "fan", "room": "kitchen", "action": "turn_on"}, code)

    assert text.strip()
    assert text.rstrip().endswith(".")


# =============================================================================
# 2. Thiết bị không có trong phòng đó - ca gốc
# =============================================================================

def test_light_in_main_door_explains_and_offers_alternatives() -> None:
    text = fails(
        {"device": "light", "room": "main_door", "action": "turn_on"},
        "unknown_device",
    )

    assert "không có đèn" in text
    assert "phòng khách" in text
    assert "phòng ngủ" in text


def test_alternatives_exclude_rooms_without_the_device() -> None:
    """Không được gợi ý ngược lại chính nơi vừa báo là không có."""
    text = fails(
        {"device": "fan", "room": "main_door", "action": "turn_on"},
        "unknown_device",
    )

    # "cửa chính" chỉ được xuất hiện ở vế đầu (nơi bị từ chối), không ở vế gợi ý.
    suggestion_part = text.split(".", 1)[1] if "." in text else ""
    assert "cửa chính" not in suggestion_part


# =============================================================================
# 3. Phòng không có trong registry
# =============================================================================

def test_unknown_room_with_known_device_suggests_valid_rooms() -> None:
    text = fails(
        {"device": "light", "room": "kitchen", "action": "turn_on"}, "unknown_room"
    )

    assert "phòng bếp" in text
    assert "phòng khách" in text


def test_unknown_room_without_device_lists_all_rooms() -> None:
    text = fails({"device": None, "room": "kitchen", "action": None}, "unknown_room")

    assert "phòng khách" in text
    assert "phòng ngủ" in text


def test_unmappable_room_does_not_leak_raw_value() -> None:
    """
    Phòng không có trong display_names -> không được in chuỗi thô ra.

    Chuỗi đó có thể là bất cứ thứ gì model trả về, kể cả nội dung người dùng
    dán vào. Không echo lại đầu vào chưa kiểm soát.
    """
    text = fails(
        {"device": "light", "room": "<script>xyz</script>", "action": "turn_on"},
        "unknown_room",
    )

    assert "script" not in text
    assert "xyz" not in text


# =============================================================================
# 4. Hành động không hỗ trợ
# =============================================================================

def test_unsupported_action_lists_what_is_possible() -> None:
    text = fails(
        {"device": "door", "room": "main_door", "action": "turn_on"},
        "unsupported_action",
    )

    assert "mở" in text
    assert "đóng" in text


def test_unsupported_action_avoids_duplicated_names() -> None:
    """
    devices.door và rooms.main_door cùng hiển thị "cửa chính".

    Không được sinh ra "Cửa chính ở cửa chính không làm được việc đó."
    """
    text = fails(
        {"device": "door", "room": "main_door", "action": "turn_on"},
        "unsupported_action",
    )

    assert "cửa chính ở cửa chính" not in text.lower()


# =============================================================================
# 5. Helper ghép tên
# =============================================================================

def test_device_in_room_text_joins_normally() -> None:
    assert device_in_room_text("light", "living_room") == "đèn phòng khách"
    assert device_in_room_text("light", "living_room", joiner=" ở ") == (
        "đèn ở phòng khách"
    )


def test_device_in_room_text_collapses_nested_names() -> None:
    """Trùng hoặc lồng tên -> giữ tên dài hơn."""
    result = device_in_room_text("door", "main_door", joiner=" ở ")

    assert result == "cửa chính"


def test_device_in_room_text_without_room() -> None:
    assert device_in_room_text("fan", None) == "quạt"


# =============================================================================
# 6. Registry thật
# =============================================================================

def test_real_registry_light_in_main_door() -> None:
    real = load_device_registry()

    if "light" in real.get("main_door", {}):
        pytest.skip("Registry thật đã đổi; ca này không còn áp dụng.")

    text = failure_response(
        {"device": "light", "room": "main_door", "action": "turn_on"},
        {"code": "unknown_device"},
        real,
    )

    assert "đã bật" not in text.lower()
    assert "đèn" in text