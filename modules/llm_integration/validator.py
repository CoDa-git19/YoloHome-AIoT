from __future__ import annotations
from config.capabilities import action_requires_face_auth, resolve_action_capability
import json
from pathlib import Path
from typing import Any
from copy import deepcopy
from functools import lru_cache
from config.settings import COMMAND_SCHEMA_PATH


def load_command_schema(schema_path: Path) -> dict[str, Any]:
    """Tải động file cấu hình command_schema.json."""
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _cached_default_command_schema() -> dict[str, Any]:
    """
    Cache nội bộ cho command_schema.json.

    Không dùng trực tiếp: luôn đi qua load_default_command_schema()
    để tránh trả về object dùng chung cho mọi caller.
    """
    return load_command_schema(COMMAND_SCHEMA_PATH)


def load_default_command_schema() -> dict[str, Any]:
    """
    Load the default command schema from config/command_schema.json.

    Trả về bản sao để caller không vô tình mutate cache dùng chung.
    Việc đọc đĩa vẫn được cache, chỉ deepcopy một dict nhỏ.
    """
    return deepcopy(_cached_default_command_schema())


def get_valid_sensors(command_schema: dict[str, Any]) -> set[str]:
    """Return valid automation sensor names from command schema config."""
    return set(command_schema.get("valid_sensors", []))


def get_valid_operators(command_schema: dict[str, Any]) -> set[str]:
    """Return valid automation operators from command schema config."""
    return set(command_schema.get("valid_operators", []))


def normalize_command(
    command: dict[str, Any],
    command_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Chuẩn hóa output của LLM khớp schema dự án.

    Mọi required_field bị thiếu sẽ được điền None (thay vì để validator
    báo missing_field), giúp pipeline có cơ hội chuyển sang clarify.
    """
    if command_schema is None:
        command_schema = load_default_command_schema()

    normalized = dict(command)

    # Tương thích ngược: LLM đôi khi trả requires_auth thay vì face_auth
    if "face_auth" not in normalized and "requires_auth" in normalized:
        normalized["face_auth"] = bool(normalized.pop("requires_auth"))

    for field in command_schema.get("required_fields", []):
        normalized.setdefault(field, None)

    if normalized.get("face_auth") is None:
        normalized["face_auth"] = False

    if normalized.get("response") is None:
        normalized["response"] = ""

    return normalized


# =============================================================================
# Server-side security policy enforcement
#
# Nguyên tắc: LLM là bộ HIỂU Ý ĐỊNH, không phải bộ RA QUYẾT ĐỊNH AN NINH.
# Cờ face_auth do server quyết định dựa trên device_capabilities.json +
# command_schema.sensitive_actions, KHÔNG tin giá trị LLM trả về.
# =============================================================================

POLICY_INTENTS = {"control_device", "query_status", "create_rule", "schedule"}

# Giới hạn cho lệnh hẹn giờ.
#
# Cận dưới: dưới 5 giây thì người dùng không phân biệt được với lệnh chạy ngay,
# và vòng poll 2 giây có thể bỏ lỡ.
# Cận trên: 24 giờ. Lịch dài hơn cần giao diện quản lý và cách huỷ tử tế, mà
# hệ thống chưa có - xem Limitations.
MIN_SCHEDULE_DELAY_SECONDS = 5
MAX_SCHEDULE_DELAY_SECONDS = 24 * 60 * 60


def requires_face_auth_by_policy(
    device: str,
    action: str,
    command_schema: dict[str, Any] | None = None,
) -> bool:
    """
    Nguồn sự thật duy nhất cho câu hỏi: hành động này có cần Face Auth không?

    Kết hợp 2 nguồn config:
    - command_schema["sensitive_actions"]
    - device_capabilities.json (qua action_requires_face_auth)
    """
    if command_schema is None:
        command_schema = load_default_command_schema()

    for sensitive_action in command_schema.get("sensitive_actions", []):
        if (
            sensitive_action.get("device") == device
            and sensitive_action.get("action") == action
            and sensitive_action.get("face_auth") is True
        ):
            return True

    try:
        return action_requires_face_auth(device=device, action=action)
    except ValueError:
        return False


def enforce_policy(
    command: dict[str, Any],
    command_schema: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    Ghi đè face_auth theo policy phía server, bất kể LLM trả về gì.

    Ghi đè theo CẢ HAI CHIỀU:
    - Action cần auth nhưng LLM trả face_auth=false -> ép True.
      (LLM quên, hoặc user cố prompt-injection để né xác thực.)
    - Action không cần auth nhưng LLM trả face_auth=true -> ép False.
      (Tránh LLM bắt xác thực khuôn mặt cho mọi lệnh vặt.)

    Returns:
        (command đã enforce, danh sách mô tả các lần ghi đè)
    """
    if command_schema is None:
        command_schema = load_default_command_schema()

    enforced = dict(command)
    overrides: list[str] = []

    intent = enforced.get("intent")
    device = enforced.get("device")
    action = enforced.get("action")

    if intent not in POLICY_INTENTS or not device or not action:
        return enforced, overrides

    required = requires_face_auth_by_policy(
        device=device,
        action=action,
        command_schema=command_schema,
    )
    claimed = enforced.get("face_auth") is True

    if required and not claimed:
        enforced["face_auth"] = True
        overrides.append(
            f"face_auth forced True for {device}.{action}: "
            "policy requires face authentication, but the LLM returned face_auth=false."
        )
    elif not required and claimed:
        enforced["face_auth"] = False
        overrides.append(
            f"face_auth forced False for {device}.{action}: "
            "policy does not require face authentication."
        )

    return enforced, overrides


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
    command = normalize_command(command, command_schema=command_schema)

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
    
    # router chuyển sang clarify, không phải lỗi hệ thống
    if intent in {"control_device", "query_status", "create_rule", "schedule"}:
        if device is None or room is None or action is None:
            return {
                "passed": False,
                "code": "missing_slot",
                "message": "Command is missing device, room, or action.",
            }

    # 3. Ràng buộc: query_status bắt buộc phải đi kèm action get_status
    if intent == "query_status" and action != "get_status":
        return {
            "passed": False,
            "code": "invalid_query_action",
            "message": "query_status intent must use action=get_status.",
        }

    # 3b. Lệnh hẹn giờ: chạy KHÔNG CÓ NGƯỜI, nên cùng ràng buộc với create_rule.
    if intent == "schedule":
        # Cùng lý do đã chặn automation rule: lệnh hẹn giờ thực thi lúc không
        # ai đứng trước camera, nên không thể xác thực khuôn mặt. Thiếu chốt
        # này thì "sau 5 phút mở cửa chính" sẽ mở được cửa - đúng lỗ hổng mà
        # Layer 3 đã đóng cho automation rule, mở lại qua một đường mới.
        if requires_face_auth_by_policy(
            device=device,
            action=action,
            command_schema=command_schema,
        ):
            return {
                "passed": False,
                "code": "safety_rule_violation",
                "message": (
                    f"Cannot schedule '{action}' on device '{device}': the "
                    "action requires face authentication, and a scheduled "
                    "command runs unattended."
                ),
            }

        delay = command.get("delay_seconds")
        if not isinstance(delay, int) or isinstance(delay, bool):
            return {
                "passed": False,
                "code": "invalid_delay",
                "message": "schedule intent requires an integer delay_seconds.",
            }

        if delay < MIN_SCHEDULE_DELAY_SECONDS:
            return {
                "passed": False,
                "code": "invalid_delay",
                "message": (
                    f"delay_seconds={delay} is too small; minimum is "
                    f"{MIN_SCHEDULE_DELAY_SECONDS}."
                ),
            }

        if delay > MAX_SCHEDULE_DELAY_SECONDS:
            return {
                "passed": False,
                "code": "invalid_delay",
                "message": (
                    f"delay_seconds={delay} exceeds the maximum of "
                    f"{MAX_SCHEDULE_DELAY_SECONDS} (24 hours)."
                ),
            }

    # 4. Kiểm tra cấu trúc điều kiện nếu là lệnh tạo tự động hóa (create_rule)
    if intent == "create_rule":
        # Không cho phép tạo automation rule cho hành động cần Face Auth:
        # rule chạy tự động nên không có ai đứng trước camera để xác thực.
        if requires_face_auth_by_policy(
            device=device,
            action=action,
            command_schema=command_schema,
        ):
            return {
                "passed": False,
                "code": "safety_rule_violation",
                "message": (
                    f"Cannot create an automation rule for '{action}' on "
                    f"device '{device}': the action requires face authentication."
                ),
            }

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

    if code in {"missing_field", "invalid_intent", "llm_parse_error"}:
        return "error: llm_parse"

    if code == "llm_timeout":
        return "error: llm_timeout"

    if code == "llm_api_error":
        return "error: llm_api"

    if code == "llm_unavailable":
        return "error: llm_unavailable"

    if code == "llm_rate_limited":
        return "error: llm_rate_limit"
    
    if code == "missing_slot":
        return "clarify: missing_slot"

    return "fail: validation"