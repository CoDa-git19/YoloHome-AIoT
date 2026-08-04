"""
Test cho phát hiện phòng chưa đăng ký (detect_unsupported_room).

CHẠY OFFLINE.

QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
---------------------------
    Người dùng nhắc tới một phòng có TÊN HỢP LỆ nhưng registry chưa có
    -> luôn là registry_request, do SERVER quyết định, không phụ thuộc engine.

Lỗi gốc, quan sát được khi chạy console với Gemini:

    Bot : Bạn muốn bật đèn ở phòng nào? Hiện có đèn ở: phòng ngủ, phòng khách.
    User: nhà bếp
    Bot : Bạn muốn bật đèn ở phòng nào? Hiện có đèn ở: phòng ngủ, phòng khách.
    User: nhà bếp
    Bot : (lặp lại y nguyên lần nữa)

Bot không hề thừa nhận là phòng đó không tồn tại. Người dùng tưởng máy treo nên
gõ lại, đốt hết SESSION_MAX_TURNS rồi bị cắt phiên.

Mock parser vốn làm ĐÚNG (xem mentions_room_like), chỉ Gemini là lệch. Đưa
quyết định về server thì hai engine hành xử giống nhau.
"""

from __future__ import annotations

import pytest

from modules.llm_integration.llm_module import (
    detect_unsupported_room,
    load_device_registry,
    make_registry_request_command,
    unsupported_room_response,
)


REGISTRY = {
    "living_room": {"light": ["turn_on", "turn_off"], "fan": ["turn_on", "turn_off"]},
    "bedroom": {"light": ["turn_on", "turn_off"]},
    "main_door": {"door": ["open", "close"]},
}


def detect(text: str) -> str | None:
    return detect_unsupported_room(text, REGISTRY)


# =============================================================================
# 1. Nhận ra phòng chưa đăng ký
# =============================================================================

@pytest.mark.parametrize(
    "text,expected",
    [
        ("nhà bếp", "kitchen"),
        ("bật đèn nhà bếp", "kitchen"),
        ("phòng bếp", "kitchen"),
        ("phòng tắm", "bathroom"),
        ("nhà tắm", "bathroom"),
        ("phòng làm việc", "office"),
        ("văn phòng", "office"),
    ],
)
def test_detects_rooms_that_exist_in_aliases_but_not_in_registry(
    text: str, expected: str
) -> None:
    assert detect(text) == expected


def test_detection_is_case_insensitive() -> None:
    assert detect("NHÀ BẾP") == "kitchen"


# =============================================================================
# 2. Không được nhận nhầm phòng hợp lệ
# =============================================================================

@pytest.mark.parametrize(
    "text",
    ["phòng khách", "phòng ngủ", "cửa chính", "bật đèn phòng khách", "bật đèn", "ok", ""],
)
def test_supported_or_absent_rooms_return_none(text: str) -> None:
    """
    Phòng CÓ trong registry không phải ca này. Trả về giá trị khác None sẽ
    khiến lệnh hợp lệ bị chuyển thành registry_request.
    """
    assert detect(text) is None


def test_room_added_to_registry_stops_being_unsupported() -> None:
    """Thêm phòng vào registry là đủ - không phải sửa code."""
    extended = {**REGISTRY, "kitchen": {"light": ["turn_on", "turn_off"]}}

    assert detect_unsupported_room("nhà bếp", extended) is None


# =============================================================================
# 3. Câu trả lời hai vế
# =============================================================================

def test_response_names_the_room_and_asks_for_confirmation() -> None:
    text = unsupported_room_response("kitchen", "light", REGISTRY, "turn_on")

    assert "phòng bếp" in text
    assert "?" in text


def test_response_offers_a_way_forward() -> None:
    """
    Vế gợi ý giữ cho hội thoại không thành ngõ cụt kể cả khi người dùng
    không muốn đăng ký gì.
    """
    text = unsupported_room_response("kitchen", "light", REGISTRY, "turn_on")

    assert "phòng khách" in text
    assert "phòng ngủ" in text


def test_suggestions_are_filtered_by_the_device() -> None:
    """Không gợi ý nơi không có thiết bị đó."""
    text = unsupported_room_response("kitchen", "light", REGISTRY, "turn_on")

    assert "cửa chính" not in text


def test_response_without_a_known_device_lists_all_rooms() -> None:
    text = unsupported_room_response("bathroom", None, REGISTRY, None)

    assert "phòng tắm" in text
    assert "phòng khách" in text


def test_response_reuses_the_verb() -> None:
    assert "tắt" in unsupported_room_response("kitchen", "light", REGISTRY, "turn_off")


# =============================================================================
# 4. Yêu cầu đăng ký phải có nội dung
# =============================================================================

def test_registry_request_carries_the_requested_slots() -> None:
    """
    Ca quan sát được trong dữ liệu thật:

        (1902, 'nhà bếp', 'registry_request', 'waiting_admin_review')

    device và room đều NULL - quản trị viên đọc bảng không biết đăng ký gì.
    """
    command = make_registry_request_command(
        response="...", action="turn_on", device="light", room="kitchen"
    )

    assert command["room"] == "kitchen"
    assert command["device"] == "light"
    assert command["action"] == "turn_on"
    assert command["intent"] == "registry_request"


def test_registry_request_never_requests_face_auth() -> None:
    command = make_registry_request_command("...", device="door", room="kitchen")

    assert command["face_auth"] is False
    assert command["condition"] is None


def test_registry_request_defaults_stay_backward_compatible() -> None:
    """Lời gọi cũ chỉ truyền response vẫn phải chạy."""
    command = make_registry_request_command("xin chào")

    assert command["response"] == "xin chào"
    assert command["room"] is None


# =============================================================================
# 5. Registry thật
# =============================================================================

def test_real_registry_kitchen_is_unsupported() -> None:
    real = load_device_registry()

    if "kitchen" in real:
        pytest.skip("Nhóm đã thêm kitchen vào registry.")

    assert detect_unsupported_room("nhà bếp", real) == "kitchen"
    assert detect_unsupported_room("phòng khách", real) is None