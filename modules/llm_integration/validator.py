from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Các hằng số bổ trợ cho việc validate cấu trúc condition
VALID_SENSORS = {"temperature", "humidity", "light", "motion"}
VALID_OPERATORS = {">", "<", ">=", "<=", "=="}


def load_command_schema(schema_path: Path) -> dict[str, Any]:
    """Tải động file cấu hình command_schema.json."""
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def validate_condition(condition: Any) -> dict[str, Any]:
    """
    Kiểm tra tính hợp lệ của cấu trúc điều kiện (condition) trong lệnh tự động hóa.
    """
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

    if condition["sensor"] not in VALID_SENSORS:
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": f"Unsupported sensor: {condition['sensor']}",
        }

    if condition["operator"] not in VALID_OPERATORS:
        return {
            "passed": False,
            "code": "invalid_condition",
            "message": f"Unsupported operator: {condition['operator']}",
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

    if intent in {"clarify", "reject"}:
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
        condition_result = validate_condition(command.get("condition"))
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

    # 6. Kiểm tra quy tắc an toàn động từ mảng sensitive_actions trong file JSON cấu hình
    sensitive_actions = command_schema.get("sensitive_actions", [])
    for rule in sensitive_actions:
        if device == rule.get("device") and action == rule.get("action"):
            if rule.get("face_auth") is True and command.get("face_auth") is not True:
                return {
                    "passed": False,
                    "code": "safety_rule_violation",
                    "message": f"The action '{action}' on device '{device}' requires face authentication.",
                }

    return {
        "passed": True,
        "code": "valid",
        "message": "Command schema and device are valid.",
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