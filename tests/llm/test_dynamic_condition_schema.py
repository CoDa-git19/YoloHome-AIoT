from modules.llm_integration.validator import (
    get_valid_operators,
    get_valid_sensors,
    validate_command,
    validate_condition,
)


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
        "registry_request",
    ],
    "valid_sensors": [
        "temperature",
        "humidity",
    ],
    "valid_operators": [
        ">",
        "<",
    ],
    "sensitive_actions": [],
}


DEVICE_REGISTRY = {
    "living_room": {
        "fan": ["turn_on", "turn_off", "get_status"],
    }
}


def _rule_command(
    *,
    sensor: str,
    operator: str,
    value: int | float = 30,
) -> dict:
    return {
        "intent": "create_rule",
        "action": "turn_on",
        "device": "fan",
        "room": "living_room",
        "face_auth": False,
        "condition": {
            "sensor": sensor,
            "operator": operator,
            "value": value,
        },
        "response": "Đã tạo luật tự động hóa.",
    }


def test_condition_schema_exposes_valid_sensors_and_operators():
    assert get_valid_sensors(COMMAND_SCHEMA) == {"temperature", "humidity"}
    assert get_valid_operators(COMMAND_SCHEMA) == {">", "<"}


def test_validate_condition_uses_schema_sensor_allowlist():
    result = validate_condition(
        {
            "sensor": "humidity",
            "operator": ">",
            "value": 70,
        },
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is True
    assert result["code"] == "valid_condition"


def test_validate_condition_rejects_sensor_not_in_schema():
    result = validate_condition(
        {
            "sensor": "motion",
            "operator": ">",
            "value": 1,
        },
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is False
    assert result["code"] == "invalid_condition"
    assert "Unsupported sensor" in result["message"]


def test_validate_condition_rejects_operator_not_in_schema():
    result = validate_condition(
        {
            "sensor": "temperature",
            "operator": ">=",
            "value": 30,
        },
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is False
    assert result["code"] == "invalid_condition"
    assert "Unsupported operator" in result["message"]


def test_create_rule_uses_condition_schema_from_command_schema():
    result = validate_command(
        _rule_command(
            sensor="humidity",
            operator=">",
            value=70,
        ),
        device_registry=DEVICE_REGISTRY,
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is True
    assert result["code"] == "valid"


def test_create_rule_rejects_sensor_not_in_command_schema():
    result = validate_command(
        _rule_command(
            sensor="motion",
            operator=">",
            value=1,
        ),
        device_registry=DEVICE_REGISTRY,
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is False
    assert result["code"] == "invalid_condition"


def test_create_rule_rejects_operator_not_in_command_schema():
    result = validate_command(
        _rule_command(
            sensor="temperature",
            operator=">=",
            value=30,
        ),
        device_registry=DEVICE_REGISTRY,
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is False
    assert result["code"] == "invalid_condition"