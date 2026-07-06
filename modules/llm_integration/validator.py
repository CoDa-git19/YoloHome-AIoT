from __future__ import annotations
from config.capabilities import action_requires_face_auth, resolve_action_capability
import json
from pathlib import Path
from typing import Any
from config.settings import COMMAND_SCHEMA_PATH


def load_command_schema(schema_path: Path) -> dict[str, Any]:
    """Tải động file cấu hình command_schema.json."""
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_default_command_schema() -> dict[str, Any]:
    """Load the default command schema from config/command_schema.json."""
    return load_command_schema(COMMAND_SCHEMA_PATH)


def get_valid_sensors(command_schema: dict[str, Any]) -> set[str]:
    """Return valid automation sensor names from command schema config."""
    return set(command_schema.get("valid_sensors", []))


def get_valid_operators(command_schema: dict[str, Any]) -> set[str]:
    """Return valid automation operators from command schema config."""
    return set(command_schema.get("valid_operators", []))


def normalize_command(command: dict[str, Any]) -> dict[str, Any]:
    """
    Chuẩn hóa kết quả đầu ra của LLM khớp với schema dự án.
    """
    normalized = dict(command)

    if "face_auth" not in normalized and "requires_auth" in normalized:
        normalized["face_auth"] = bool(normalized.pop("requires_auth"))

    normalized.setdefault("condition", None)
    normalized.setdefault("response", "")

    return normalized


def validate_condition(
    condition: Any,
    command_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Validate an automation condition using command_schema.json.

    The valid sensors and operators are config-driven:
    - command_schema["valid_sensors"]
    - command_schema["valid_operators"]
    """
    if command_schema is None:
        command_schema = load_default_command_schema()

    if not isinstance(condition, dict):
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": "Condition must be a valid JSON object.",
        }

    required = {"sensor", "operator", "value"}
    missing = required - set(condition.keys())
    if missing:
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": f"Missing condition fields: {sorted(missing)}",
        }

    if condition.get("value") is None:
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": "Condition value must not be null.",
        }

    valid_sensors = get_valid_sensors(command_schema)
    valid_operators = get_valid_operators(command_schema)

    if condition["sensor"] not in valid_sensors:
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": f"Unsupported sensor: {condition['sensor']}",
        }

    if condition["operator"] not in valid_operators:
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": f"Unsupported operator: {condition['operator']}",
        }

    try:
        float(condition["value"])
    except (TypeError, ValueError):
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": "Condition value must be numeric.",
        }

    return {
        "passed": True,
        "code": "valid_condition",
        "message": "Condition is valid.",
    }


def validate_command(
    command: dict[str, Any],
    device_registry: dict[str, dict[str, list[str]]],
    command_schema: dict[str, Any],
) -> dict[str, Any]:
    """
    Hàm validate động dựa trên device_registry và command_schema.json.
    """
    command = normalize_command(command)

    # 1. Kiểm tra các trường bắt buộc từ file JSON cấu hình
    required_fields = set(command_schema.get("required_fields", []))
    missing_fields = required_fields - set(command.keys())
    if missing_fields:
        return {
            "passed": False,
            "code": "missing_field",
            "message": f"Missing fields: {sorted(missing_fields)}",
        }

    # 2. Kiểm tra Intent hợp lệ từ file JSON cấu hình
    valid_intents = set(command_schema.get("intents", []))
    intent = command.get("intent")
    if intent not in valid_intents:
        return {
            "passed": False,
            "code": "invalid_intent",
            "message": f"Invalid intent: {intent}",
        }

    if intent in {"clarify", "reject", "registry_request"}:
        return {
            "passed": True,
            "code": intent,
            "message": "No hardware execution required.",
        }

    room = command.get("room")
    device = command.get("device")
    action = command.get("action")

    # 3. Ràng buộc: query_status bắt buộc phải đi kèm action get_status
    if intent == "query_status" and action != "get_status":
        return {
            "passed": False,
            "code": "invalid_query_action",
            "message": "query_status intent must use action=get_status.",
        }

    # 4. Kiểm tra cấu trúc điều kiện nếu là lệnh tạo tự động hóa (create_rule)
    if intent == "create_rule":
        condition_result = validate_condition(
            command.get("condition"),
            command_schema=command_schema,
        )
        if not condition_result["passed"]:
            return condition_result

    # 5. Kiểm tra phòng và thiết bị hợp lệ dựa trên Device Registry
    if room not in device_registry:
        return {
            "passed": False,
            "code": "unknown_room",
            "message": f"Unsupported room: {room}",
        }

    if device not in device_registry[room]:
        return {
            "passed": False,
            "code": "unknown_device",
            "message": f"Unsupported device: {device} in room: {room}",
        }

    allowed_actions = device_registry[room][device]
    if action not in allowed_actions:
        return {
            "passed": False,
            "code": "unsupported_action",
            "message": f"Unsupported action: {action} for {room}.{device}",
        }

    try:
        resolve_action_capability(device=device, action=action)
    except ValueError as exc:
        return {
            "passed": False,
            "code": "unsupported_action",
            "message": str(exc),
        }

    # 6. Kiểm tra quy tắc an toàn động từ mảng sensitive_actions trong file JSON cấu hình
    for sensitive_action in command_schema.get("sensitive_actions", []):
        if (
            sensitive_action.get("device") == device
            and sensitive_action.get("action") == action
            and sensitive_action.get("face_auth") is True
            and command.get("face_auth") is not True
        ):
            return {
                "passed": False,
                "code": "safety_rule_violation",
                "message": (
                    f"The action '{action}' on device '{device}' requires "
                    "face authentication."
                ),
            }

    try:
        requires_auth_by_capability = action_requires_face_auth(
            device=device,
            action=action,
        )
    except ValueError:
        requires_auth_by_capability = False

    if requires_auth_by_capability and command.get("face_auth") is not True:
        return {
            "passed": False,
            "code": "safety_rule_violation",
            "message": (
                f"The action '{action}' on device '{device}' requires "
                "face authentication by device capability policy."
            ),
        }

    return {
        "passed": True,
        "code": "valid",
        "message": "Command is valid.",
    }


def validation_code_to_log_result(code: str) -> str:
    """Map mã lỗi validate sang kết quả lưu cơ sở dữ liệu."""
    if code in {"unknown_device", "unknown_room", "unsupported_action"}:
        return "rejected: unknown_device"

    if code in {"safety_rule_violation", "invalid_query_action", "invalid_condition"}:
        return "rejected: validation"

    if code in {"missing_field", "invalid_intent"}:
        return "error: llm_parse"

    return "fail: validation"