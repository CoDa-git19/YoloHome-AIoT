from modules.llm_integration.validator import validate_command


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
    "sensitive_actions": [],
}


def _command(
    *,
    device: str,
    action: str,
    room: str,
    face_auth: bool,
) -> dict:
    return {
        "intent": "control_device",
        "action": action,
        "device": device,
        "room": room,
        "face_auth": face_auth,
        "condition": None,
        "response": "OK",
    }


def test_entry_point_open_requires_face_auth_by_capability():
    registry = {
        "garage": {
            "garage_door": ["open", "close", "get_status"],
        }
    }

    result = validate_command(
        _command(
            device="garage_door",
            action="open",
            room="garage",
            face_auth=False,
        ),
        device_registry=registry,
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is False
    assert result["code"] == "safety_rule_violation"


def test_entry_point_open_passes_with_face_auth():
    registry = {
        "garage": {
            "garage_door": ["open", "close", "get_status"],
        }
    }

    result = validate_command(
        _command(
            device="garage_door",
            action="open",
            room="garage",
            face_auth=True,
        ),
        device_registry=registry,
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is True
    assert result["code"] == "valid"


def test_cover_open_does_not_require_face_auth():
    registry = {
        "living_room": {
            "curtain": ["open", "close", "get_status"],
        }
    }

    result = validate_command(
        _command(
            device="curtain",
            action="open",
            room="living_room",
            face_auth=False,
        ),
        device_registry=registry,
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is True
    assert result["code"] == "valid"


def test_registry_action_without_capability_policy_is_rejected():
    registry = {
        "living_room": {
            "light": ["set_brightness"],
        }
    }

    result = validate_command(
        _command(
            device="light",
            action="set_brightness",
            room="living_room",
            face_auth=False,
        ),
        device_registry=registry,
        command_schema=COMMAND_SCHEMA,
    )

    assert result["passed"] is False
    assert result["code"] == "unsupported_action"