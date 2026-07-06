from modules.llm_integration.validator import validate_command


DEVICE_REGISTRY = {
    "living_room": {
        "light": ["turn_on", "turn_off", "get_status"],
        "fan": ["turn_on", "turn_off", "get_status"],
    },
    "main_door": {
        "door": ["open", "close", "get_status"],
    },
}


COMMAND_SCHEMA = {
    "required_fields": [
        "intent",
        "action",
        "device",
        "room",
        "face_auth",
        "condition",
        "response",
    ],
    "intents": [
        "control_device",
        "query_status",
        "create_rule",
        "clarify",
        "reject",
    ],
    "sensitive_actions": [
        {
            "device": "door",
            "action": "open",
            "face_auth": True,
        }
    ],
}


def test_valid_light_command():
    command = {
        "intent": "control_device",
        "action": "turn_on",
        "device": "light",
        "room": "living_room",
        "face_auth": False,
        "condition": None,
        "response": "Đã bật đèn phòng khách.",
    }

    # Đã cập nhật truyền đủ 3 tham số theo định dạng mới của validator.py
    result = validate_command(command, DEVICE_REGISTRY, COMMAND_SCHEMA)

    assert result["passed"] is True
    assert result["code"] == "valid"


def test_reject_unknown_device():
    command = {
        "intent": "control_device",
        "action": "turn_on",
        "device": "air_conditioner",
        "room": "living_room",
        "face_auth": False,
        "condition": None,
        "response": "Thiết bị không hỗ trợ.",
    }

    result = validate_command(command, DEVICE_REGISTRY, COMMAND_SCHEMA)

    assert result["passed"] is False
    assert result["code"] == "unknown_device"


def test_door_open_requires_face_auth():
    """Kiểm tra quy tắc an toàn: mở cửa bắt buộc phải yêu cầu xác thực khuôn mặt."""
    command = {
        "intent": "control_device",
        "action": "open",
        "device": "door",
        "room": "main_door",
        "face_auth": False,  # Vi phạm quy tắc an toàn
        "condition": None,
        "response": "Mở cửa chính.",
    }

    result = validate_command(command, DEVICE_REGISTRY, COMMAND_SCHEMA)

    assert result["passed"] is False
    assert result["code"] == "safety_rule_violation"


def test_invalid_query_action():
    """Kiểm tra validator chặn query_status nhưng action khác get_status."""
    bad_command = {
        "intent": "query_status",
        "action": "turn_on",
        "device": "light",
        "room": "living_room",
        "face_auth": False,
        "condition": None,
        "response": "Đang kiểm tra.",
    }

    result = validate_command(bad_command, DEVICE_REGISTRY, COMMAND_SCHEMA)

    assert result["passed"] is False
    assert result["code"] == "invalid_query_action"


def test_valid_create_rule_condition():
    command = {
        "intent": "create_rule",
        "action": "turn_on",
        "device": "fan",
        "room": "living_room",
        "face_auth": False,
        "condition": {
            "sensor": "temperature",
            "operator": ">",
            "value": 30,
        },
        "response": "Đã tạo luật tự động hóa.",
    }

    result = validate_command(command, DEVICE_REGISTRY, COMMAND_SCHEMA)

    assert result["passed"] is True
    assert result["code"] == "valid"


def test_invalid_create_rule_condition():
    command = {
        "intent": "create_rule",
        "action": "turn_on",
        "device": "fan",
        "room": "living_room",
        "face_auth": False,
        "condition": {
            "sensor": "gas_sensor",  # Sensor không hỗ trợ
            "operator": ">",
            "value": 100,
        },
        "response": "Lỗi cảm biến.",
    }

    result = validate_command(command, DEVICE_REGISTRY, COMMAND_SCHEMA)

    assert result["passed"] is False
    assert result["code"] == "invalid_condition"