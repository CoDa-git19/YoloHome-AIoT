"""
Test cho việc phân biệt tên phòng với tên thiết bị khi chúng TRÙNG CHUỖI.

CHẠY OFFLINE.

QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
---------------------------
    Phần chữ mô tả PHÒNG không được nuốt mất phần chữ mô tả THIẾT BỊ.

Lỗi gốc, quan sát được khi chạy console ở chế độ mock:

    Nhập lệnh: bật đèn cửa chính
    [Pipeline] next_step = auth_required
    [FaceID] Security clearance required. Activating camera...

"cửa chính" vừa là phòng main_door vừa là alias của thiết bị door. Longest-match
chọn "cửa chính" (9 ký tự) thắng "đèn" (3 ký tự), nên device='door'. Cộng với
"bật" nằm trong open_keywords -> action='open' -> door.open -> hành động NHẠY CẢM
duy nhất của hệ thống.

Một câu về đèn biến thành lệnh mở cửa. Hôm nay fail-closed che lại vì face module
chưa bật; khi nối model thật thì nó sẽ bật camera đòi quét mặt.
"""

from __future__ import annotations

import pytest

from modules.llm_integration.llm_module import (
    detect_device,
    detect_device_excluding_room,
    detect_room,
    detect_room_with_alias,
    load_device_registry,
)


REGISTRY = {
    "living_room": {"light": ["turn_on", "turn_off"], "fan": ["turn_on", "turn_off"]},
    "bedroom": {"light": ["turn_on", "turn_off"]},
    "main_door": {"door": ["open", "close"]},
}


def device_of(text: str) -> str | None:
    return detect_device_excluding_room(text, REGISTRY)


# =============================================================================
# 1. Ca gốc
# =============================================================================

def test_light_command_is_not_swallowed_by_the_room_name() -> None:
    """
    NGHIÊM TRỌNG nếu đỏ: "bật đèn cửa chính" bị hiểu thành mở cửa chính.
    """
    assert device_of("bật đèn cửa chính") == "light"


def test_fan_command_is_not_swallowed_either() -> None:
    assert device_of("bật quạt cửa chính") == "fan"


@pytest.mark.parametrize(
    "text",
    ["bật đèn cửa chính", "tắt đèn ở cửa chính", "bật quạt cửa chính"],
)
def test_no_device_command_ever_resolves_to_door(text: str) -> None:
    """Không câu nào nhắc tới đèn/quạt được phép ra thiết bị door."""
    assert device_of(text) != "door"


# =============================================================================
# 2. Không được phá lệnh cửa thật
# =============================================================================

@pytest.mark.parametrize(
    "text", ["mở cửa chính", "đóng cửa chính", "cửa chính", "khóa cửa chính"]
)
def test_real_door_commands_still_resolve_to_door(text: str) -> None:
    """
    Cắt tên phòng ra rồi không còn thiết bị nào -> quay lại dò trên câu đầy đủ.

    Thiếu bước lùi này thì "mở cửa chính" mất luôn thiết bị.
    """
    assert device_of(text) == "door"


def test_room_detection_is_unchanged() -> None:
    assert detect_room("bật đèn cửa chính", REGISTRY) == "main_door"
    assert detect_room("bật đèn phòng khách", REGISTRY) == "living_room"


# =============================================================================
# 3. Câu bình thường không bị ảnh hưởng
# =============================================================================

@pytest.mark.parametrize(
    "text,expected",
    [
        ("bật đèn phòng khách", "light"),
        ("tắt quạt phòng ngủ", "fan"),
        ("bật đèn", "light"),
        ("bật quạt", "fan"),
        ("phòng khách", None),
    ],
)
def test_ordinary_commands_are_untouched(text: str, expected) -> None:
    assert device_of(text) == expected


def test_matches_plain_detect_device_when_no_room_is_mentioned() -> None:
    """Không nhắc phòng thì hai hàm phải cho cùng kết quả."""
    for text in ["bật đèn", "tắt quạt", "mở cửa"]:
        assert device_of(text) == detect_device(text, REGISTRY)


# =============================================================================
# 4. detect_room_with_alias
# =============================================================================

def test_alias_is_returned_for_removal() -> None:
    room, alias = detect_room_with_alias("bật đèn cửa chính", REGISTRY)

    assert room == "main_door"
    assert alias == "cửa chính"


def test_no_room_returns_no_alias() -> None:
    assert detect_room_with_alias("bật đèn", REGISTRY) == (None, None)


# =============================================================================
# 5. Registry thật
# =============================================================================

def test_real_registry_light_at_main_door_is_not_a_door_command() -> None:
    real = load_device_registry()

    if "main_door" not in real:
        pytest.skip("Registry thật đã đổi.")

    assert detect_device_excluding_room("bật đèn cửa chính", real) != "door"