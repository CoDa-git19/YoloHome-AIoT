"""
Tests cho phần dọn nợ kỹ thuật (P2).

- P2-1: config là nguồn sự thật duy nhất (prompt không hardcode lại schema).
- P2-3: trích JSON không dùng regex tham lam.
- P2-4: special command registry đối chiếu được với config.
- P2-5: nhận diện điều kiện automation gắn số với đúng cảm biến.
"""

from __future__ import annotations

import pytest

from modules.llm_integration.llm_module import (
    build_prompt,
    detect_condition,
    detect_operator,
    extract_json_object,
    find_alias,
    load_schema,
    parse_number,
)
from system_core.commands import (
    SPECIAL_COMMANDS,
    CloseDoorCommand,
    OpenDoorCommand,
    validate_special_commands,
)


# =============================================================================
# P2-3: trích JSON
# =============================================================================

def test_extracts_plain_json():
    assert extract_json_object('{"intent": "clarify"}') == {"intent": "clarify"}


def test_extracts_json_from_markdown_fence():
    text = '```json\n{"intent": "clarify"}\n```'
    assert extract_json_object(text) == {"intent": "clarify"}


def test_extracts_json_with_trailing_prose():
    """
    Regex tham lam \\{.*\\} sẽ nuốt từ dấu { đầu tới dấu } CUỐI, nên vỡ ngay
    khi model kèm thêm văn bản có dấu ngoặc nhọn phía sau.
    """
    text = 'Kết quả: {"intent": "clarify", "device": null} Hy vọng giúp được {bạn}'
    assert extract_json_object(text) == {"intent": "clarify", "device": None}


def test_extracts_first_of_two_json_objects():
    assert extract_json_object('{"a": 1}{"b": 2}') == {"a": 1}


def test_extracts_json_with_leading_prose():
    text = 'Đây là JSON bạn cần:\n{"intent": "control_device"}'
    assert extract_json_object(text) == {"intent": "control_device"}


def test_extracts_nested_json():
    text = '{"condition": {"sensor": "temperature", "value": 30}}'
    assert extract_json_object(text)["condition"]["value"] == 30


def test_raises_when_no_json_present():
    with pytest.raises(ValueError):
        extract_json_object("xin lỗi, tôi không hiểu")


# =============================================================================
# P2-5: nhận diện điều kiện automation
# =============================================================================

def test_number_must_come_after_the_sensor():
    """
    "sau 5 phút nếu nhiệt độ trên 30" - số 5 KHÔNG phải ngưỡng.
    Trước đây parser lấy số đầu tiên trong cả câu.
    """
    condition = detect_condition("sau 5 phút nếu nhiệt độ trên 30 thì bật quạt")

    assert condition["value"] == 30


def test_number_after_the_action_is_ignored():
    condition = detect_condition(
        "nếu nhiệt độ trên 30 độ thì bật quạt 2 phòng khách"
    )

    assert condition["value"] == 30


@pytest.mark.parametrize(
    "text, operator",
    [
        ("nếu nhiệt độ trên 30 thì bật quạt", ">"),
        ("nếu nhiệt độ dưới 20 thì tắt quạt", "<"),
        ("nếu nhiệt độ lớn hơn hoặc bằng 28 thì bật quạt", ">="),
        ("nếu độ ẩm không quá 60 thì tắt quạt", "<="),
    ],
)
def test_operator_detection(text: str, operator: str):
    assert detect_condition(text)["operator"] == operator


def test_longest_operator_alias_wins():
    """
    "lớn hơn hoặc bằng" chứa "lớn hơn". Phải chọn cái DÀI hơn (>=), không phải (>).
    """
    assert detect_operator("lớn hơn hoặc bằng") == ">="


def test_operator_alias_respects_word_boundaries():
    """
    Alias "là" của toán tử == KHÔNG được khớp vào giữa chữ "làm"
    trong "phòng làm việc".
    """
    condition = detect_condition("nếu độ ẩm dưới 40 thì bật quạt phòng làm việc")

    assert condition["operator"] == "<"


def test_find_alias_respects_word_boundaries():
    assert find_alias("phòng làm việc", ["là"]) is None
    assert find_alias("nhiệt độ là 30", ["là"]) is not None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("30", 30),
        ("30.5", 30.5),
        ("30,5", 30.5),  # dấu phẩy thập phân kiểu Việt Nam
    ],
)
def test_parse_number(raw: str, expected: float):
    assert parse_number(raw) == expected


def test_vietnamese_decimal_comma():
    assert detect_condition("nếu nhiệt độ trên 30,5 độ thì bật quạt")["value"] == 30.5


def test_no_sensor_means_no_condition():
    assert detect_condition("bật đèn phòng khách") is None


def test_no_number_means_no_condition():
    assert detect_condition("nếu nhiệt độ cao thì bật quạt") is None


# =============================================================================
# P2-1: prompt lấy từ config, không hardcode
# =============================================================================

def test_prompt_lists_intents_from_command_schema():
    schema = load_schema()
    prompt = build_prompt("bật đèn", command_schema=schema)

    for intent in schema["intents"]:
        assert intent in prompt


def test_prompt_lists_sensors_and_operators_from_command_schema():
    schema = load_schema()
    prompt = build_prompt("bật đèn", command_schema=schema)

    for sensor in schema["valid_sensors"]:
        assert sensor in prompt

    for operator in schema["valid_operators"]:
        assert operator in prompt


def test_prompt_lists_sensitive_actions_from_command_schema():
    prompt = build_prompt("mở cửa chính")

    assert "door.open" in prompt


def test_prompt_adapts_when_schema_changes():
    """
    Thêm sensor mới vào command_schema.json -> prompt tự cập nhật,
    KHÔNG phải sửa prompt_template.txt.
    """
    schema = load_schema()
    schema["valid_sensors"] = schema["valid_sensors"] + ["co2"]

    prompt = build_prompt("bật đèn", command_schema=schema)

    assert "co2" in prompt


def test_prompt_includes_pending_command_for_multi_turn():
    pending = {"intent": "clarify", "device": "light", "room": None}
    prompt = build_prompt("phòng khách", pending_command=pending)

    assert "light" in prompt


def test_prompt_pending_is_null_when_absent():
    prompt = build_prompt("bật đèn phòng khách")

    assert "null" in prompt


# =============================================================================
# P2-4: special command registry
# =============================================================================

def test_registry_maps_config_to_command_classes():
    assert SPECIAL_COMMANDS[("door", "open")] is OpenDoorCommand
    assert SPECIAL_COMMANDS[("door", "close")] is CloseDoorCommand


def test_current_config_has_no_missing_special_commands():
    """Config hiện tại phải khớp hoàn toàn với registry."""
    assert validate_special_commands() == []


def test_validator_detects_special_command_without_class():
    """
    Khai báo special command trong config mà quên viết class -> phải phát hiện
    được ngay, thay vì nổ giữa lúc demo.
    """
    capabilities = {
        "special_commands": [
            {"device": "door", "action": "open"},
            {"device": "garage_door", "action": "open"},  # chưa có class
        ]
    }

    assert validate_special_commands(capabilities) == ["garage_door.open"]