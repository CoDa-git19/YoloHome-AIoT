"""
Test cho câu gợi ý khi thiếu slot (missing_slot_question).

CHẠY OFFLINE: không gọi Gemini, không cần API key.

QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
---------------------------
    Bot chỉ được gợi ý những lựa chọn CHẮC CHẮN TỒN TẠI trong device_registry.

Vi phạm quy tắc này thì bot tự dựng bẫy cho chính nó:

    Bot : Bạn muốn bật đèn ở phòng nào? Hiện hệ thống hỗ trợ:
          phòng ngủ, phòng khách, cửa chính.
    User: cửa chính
    Bot : Xin lỗi, cửa chính không có đèn.

Người dùng làm đúng lời bot dặn và vẫn bị từ chối. Đây là lỗi đã có thật
trong hệ thống, được phát hiện khi chạy thử trên console.
"""

from __future__ import annotations

import pytest

from modules.llm_integration.llm_module import (
    devices_for_room_text,
    load_device_registry,
    missing_slot_question,
    registry_devices,
    rooms_for_device,
    rooms_for_device_text,
    validate_mock_slots,
)


# Registry tối giản, độc lập với config thật để test không vỡ khi nhóm thêm
# phòng mới. Dùng đúng các khoá có trong display_names để câu chữ đọc được.
REGISTRY = {
    "living_room": {"light": ["turn_on", "turn_off"], "fan": ["turn_on"]},
    "bedroom": {"light": ["turn_on", "turn_off"]},
    "main_door": {"door": ["open", "close"]},
}


def question(device=None, room=None, action=None, registry=None) -> str:
    return missing_slot_question(
        {"device": device, "room": room, "action": action},
        REGISTRY if registry is None else registry,
    )


# =============================================================================
# 1. Thiếu phòng - chỉ gợi ý phòng CÓ thiết bị đó
# =============================================================================

def test_room_suggestion_excludes_rooms_without_the_device() -> None:
    """
    Đèn có ở phòng khách và phòng ngủ, KHÔNG có ở cửa chính.

    Đây là ca lỗi gốc phát hiện trên console.
    """
    text = question(device="light")

    assert "phòng khách" in text
    assert "phòng ngủ" in text
    assert "cửa chính" not in text, "Gợi ý một nơi không có đèn."


def test_room_suggestion_narrows_to_a_single_room() -> None:
    """Quạt chỉ có ở phòng khách -> chỉ được gợi ý đúng phòng đó."""
    text = question(device="fan")

    assert "phòng khách" in text
    assert "phòng ngủ" not in text


def test_device_in_no_room_does_not_produce_an_empty_list() -> None:
    """
    Thiết bị hợp lệ nhưng chưa phòng nào lắp.

    Không được sinh ra "Hiện có ... ở: ." - một câu hỏi không thể trả lời.
    """
    text = question(device="tv")

    assert "Hiện có" not in text
    assert text.rstrip().endswith(".")
    assert len(text) > 20


# =============================================================================
# 2. Thiếu thiết bị - chỉ gợi ý thiết bị CÓ trong phòng đó
# =============================================================================

def test_device_suggestion_is_scoped_to_the_known_room() -> None:
    """Chiều đối xứng: hỏi thiết bị ở cửa chính thì đừng kể đèn với quạt."""
    text = question(room="main_door")

    assert "đèn" not in text
    assert "quạt" not in text


def test_device_suggestion_lists_what_the_room_actually_has() -> None:
    text = question(room="living_room")

    assert "đèn" in text
    assert "quạt" in text


def test_device_suggestion_falls_back_when_room_is_unknown() -> None:
    """Chưa biết phòng -> liệt kê toàn bộ thiết bị hệ thống hỗ trợ."""
    text = question()

    assert "đèn" in text
    assert "quạt" in text


def test_unregistered_room_does_not_crash() -> None:
    """
    room="kitchen" có trong language_aliases nhưng KHÔNG có trong registry.

    Phải rơi về danh sách chung, không được ném KeyError.
    """
    text = question(room="kitchen")

    assert isinstance(text, str) and text


# =============================================================================
# 3. Bất biến: không bao giờ gợi ý thứ không tồn tại
# =============================================================================

@pytest.mark.parametrize("device", sorted(registry_devices(REGISTRY)))
def test_never_suggests_a_room_lacking_the_device(device: str) -> None:
    """
    Kiểm tra theo tính chất, không theo ví dụ.

    Với MỌI thiết bị trong registry: không phòng nào bị gợi ý mà lại thiếu
    thiết bị đó. Thêm phòng hay thiết bị mới, test này vẫn có hiệu lực.
    """
    from modules.llm_integration.llm_module import room_display_name

    text = question(device=device)
    valid_rooms = set(rooms_for_device(device, REGISTRY))

    for room in REGISTRY:
        if room in valid_rooms:
            continue

        name = room_display_name(room)
        # Bỏ qua khi tên hiển thị trùng nhau giữa các thực thể khác nhau.
        if any(room_display_name(r) == name for r in valid_rooms):
            continue

        assert name not in text, (
            f"Gợi ý {name!r} cho {device!r} nhưng nơi đó không có thiết bị này."
        )


def test_suggestions_come_from_the_registry_not_from_aliases() -> None:
    """
    language_aliases.json biết 6 phòng; device_registry chỉ có 3.

    Chênh lệch đó là CỐ Ý: alias để NHẬN RA tên phòng, registry để QUYẾT ĐỊNH
    phòng nào được hỗ trợ. Gợi ý phải lấy từ registry.
    """
    text = question(device="light")

    for absent in ("phòng bếp", "phòng làm việc", "phòng tắm"):
        assert absent not in text


# =============================================================================
# 4. Không có hai bản sao của cùng một câu hỏi
# =============================================================================

def test_mock_parser_delegates_to_missing_slot_question() -> None:
    """
    Trước đây validate_mock_slots() giữ một bản sao câu hỏi riêng, nên đường
    mock và đường Gemini trả lời khác nhau cho cùng một tình huống.
    """
    command = validate_mock_slots(
        text="bật đèn",
        action="turn_on",
        device="light",
        room=None,
        device_registry=REGISTRY,
    )

    assert command is not None
    assert command["intent"] == "clarify"
    assert command["response"] == question(device="light", action="turn_on")


def test_question_reuses_the_verb_the_user_said() -> None:
    """
    "bật đèn" -> "Bạn muốn BẬT đèn ở phòng nào?", không phải "điều khiển".

    Trước đây câu này do Gemini soạn nên nghe tự nhiên hơn nhưng KHÔNG kèm
    danh sách lựa chọn. Server soạn thì phải giữ được cả hai.
    """
    assert "bật" in question(device="light", action="turn_on")
    assert "tắt" in question(device="light", action="turn_off")


def test_question_falls_back_when_action_is_unknown() -> None:
    assert "điều khiển" in question(device="light")
    assert "điều khiển" in question(device="light", action="do_something_weird")


def test_mock_parser_returns_none_when_nothing_is_missing() -> None:
    assert (
        validate_mock_slots(
            text="bật đèn phòng khách",
            action="turn_on",
            device="light",
            room="living_room",
            device_registry=REGISTRY,
        )
        is None
    )


# =============================================================================
# 5. Registry thật của dự án
# =============================================================================

def test_real_registry_light_question_excludes_main_door() -> None:
    """Chạy trên config thật - đúng ca đã gặp khi chạy console."""
    real = load_device_registry()

    if "main_door" not in real or "light" in real.get("main_door", {}):
        pytest.skip("Registry thật đã đổi; ca này không còn áp dụng.")

    text = missing_slot_question({"device": "light", "room": None}, real)

    assert "cửa chính" not in text


def test_helper_texts_are_never_malformed() -> None:
    """Không bao giờ trả chuỗi kết thúc bằng dấu phẩy hay có dấu phẩy đôi."""
    real = load_device_registry()

    for device in registry_devices(real):
        text = rooms_for_device_text(device, real)
        assert not text.endswith(",")
        assert ",," not in text

    for room in real:
        text = devices_for_room_text(room, real)
        assert not text.endswith(",")
        assert ",," not in text