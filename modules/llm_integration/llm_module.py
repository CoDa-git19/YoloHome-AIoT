from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from config.settings import (
    COMMAND_SCHEMA_PATH,
    DEVICE_REGISTRY_PATH,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LANGUAGE_ALIASES_PATH,
    PROMPT_TEMPLATE_PATH,
    USE_MOCK_LLM,
)
from modules.llm_integration.validator import (
    load_command_schema,
    normalize_command,
    validate_command,
    validation_code_to_log_result,
)

def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_language_aliases() -> dict[str, Any]:
    """Load Vietnamese aliases for mock command parsing."""
    return load_json(LANGUAGE_ALIASES_PATH)


LANGUAGE_ALIASES = load_language_aliases()

ACTION_ALIASES = LANGUAGE_ALIASES.get("actions") or {}
ROOM_ALIASES = LANGUAGE_ALIASES.get("rooms") or {}
DEVICE_ALIASES = LANGUAGE_ALIASES.get("devices") or {}
DISPLAY_NAMES = LANGUAGE_ALIASES.get("display_names") or {}
CONDITION_ALIASES = LANGUAGE_ALIASES.get("conditions") or {}

def build_prompt(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    device_registry: dict[str, Any] | None = None,
) -> str:
    """Build Gemini prompt from template, device registry, sensor data, and transcript."""
    if device_registry is None:
        device_registry = load_device_registry()

    if sensor_data is None:
        sensor_data = {}

    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    return (
        template.replace(
            "{device_registry}",
            json.dumps(device_registry, ensure_ascii=False, indent=2),
        )
        .replace(
            "{sensor_data}", json.dumps(sensor_data, ensure_ascii=False, indent=2)
        )
        .replace("{transcript}", transcript)
    )

def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response.")

    return json.loads(match.group(0))


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def load_schema() -> dict[str, Any]:
    """Load command schema from config/command_schema.json."""
    return load_command_schema(COMMAND_SCHEMA_PATH)


def load_device_registry() -> dict[str, dict[str, list[str]]]:
    """Load allowed rooms, devices, and actions."""
    return load_json(DEVICE_REGISTRY_PATH)


def registry_rooms(device_registry: dict[str, dict[str, list[str]]]) -> set[str]:
    return set(device_registry.keys())


def registry_devices(device_registry: dict[str, dict[str, list[str]]]) -> set[str]:
    devices: set[str] = set()

    for room_devices in device_registry.values():
        devices.update(room_devices.keys())

    return devices


def alias_matches(text: str, aliases: list[str]) -> bool:
    return any(alias in text for alias in aliases)


def detect_room(
    text: str,
    device_registry: dict[str, dict[str, list[str]]],
) -> str | None:
    """
    Detect a supported room from transcript based on device_registry.

    Returns None when:
    - no room is mentioned, or
    - mentioned room is not registered.
    """
    supported_rooms = registry_rooms(device_registry)

    for room_key in supported_rooms:
        aliases = ROOM_ALIASES.get(room_key, [room_key])
        if alias_matches(text, aliases):
            return room_key

    return None


def detect_device(
    text: str,
    device_registry: dict[str, dict[str, list[str]]],
) -> str | None:
    """
    Detect a supported device from transcript based on device_registry.

    Returns None when:
    - no device is mentioned, or
    - mentioned device is not registered.
    """
    supported_devices = registry_devices(device_registry)

    for device_key in supported_devices:
        aliases = DEVICE_ALIASES.get(device_key, [device_key])
        if alias_matches(text, aliases):
            return device_key

    return None


def mentions_room_like(text: str) -> bool:
    """
    Detect whether user seems to mention a room/location.

    This is used to distinguish:
    - missing room -> clarify
    - unknown room -> registry_request
    """
    return "phòng" in text or "cửa" in text or "bếp" in text or "văn phòng" in text


def mentions_device_like(text: str) -> bool:
    """
    Detect whether user seems to mention a device.

    This uses all known aliases, including aliases not currently registered.
    The registry still decides whether the device is supported.
    """
    for aliases in DEVICE_ALIASES.values():
        if alias_matches(text, aliases):
            return True

    return False


def alias_list(alias_group: dict[str, Any], key: str) -> list[str]:
    """Return a safe alias list from config."""
    value = alias_group.get(key, [])

    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [str(item) for item in value]

    raise ValueError(f"Alias config for '{key}' must be a list or string.")


def detect_action(text: str, device: str | None) -> str | None:
    if contains_any(text, alias_list(ACTION_ALIASES, "status")):
        return "get_status"

    if device is None:
        return None

    if device == "door":
        if contains_any(text, alias_list(ACTION_ALIASES, "open_keywords")):
            return "open"

        if contains_any(text, alias_list(ACTION_ALIASES, "close_keywords")):
            return "close"

        return None

    if contains_any(text, alias_list(ACTION_ALIASES, "open_keywords")):
        return "turn_on"

    if contains_any(text, alias_list(ACTION_ALIASES, "close_keywords")):
        return "turn_off"

    return None


def detect_condition(text: str) -> dict[str, Any] | None:
    """
    Detect simple automation conditions from Vietnamese text using config aliases.
    """
    sensor_aliases = CONDITION_ALIASES.get("sensors", {})
    operator_aliases = CONDITION_ALIASES.get("operators", {})

    sensor: str | None = None
    for sensor_key, aliases in sensor_aliases.items():
        if alias_matches(text, aliases):
            sensor = sensor_key
            break

    if sensor is None:
        return None

    number_match = re.search(r"\d+(?:\.\d+)?", text)
    if not number_match:
        return None

    raw_value = number_match.group(0)
    value: int | float = float(raw_value) if "." in raw_value else int(raw_value)

    operator = ">"
    for operator_key, aliases in operator_aliases.items():
        if alias_matches(text, aliases):
            operator = operator_key
            break

    return {
        "sensor": sensor,
        "operator": operator,
        "value": value,
    }


def make_clarify_command(
    response: str,
    action: str | None = None,
    device: str | None = None,
    room: str | None = None,
) -> dict[str, Any]:
    return {
        "intent": "clarify",
        "action": action,
        "device": device,
        "room": room,
        "face_auth": False,
        "condition": None,
        "response": response,
    }


def make_registry_request_command(response: str) -> dict[str, Any]:
    return {
        "intent": "registry_request",
        "action": None,
        "device": None,
        "room": None,
        "face_auth": False,
        "condition": None,
        "response": response,
    }


def make_reject_command(response: str) -> dict[str, Any]:
    return {
        "intent": "reject",
        "action": None,
        "device": None,
        "room": None,
        "face_auth": False,
        "condition": None,
        "response": response,
    }


def device_display_name(device: str | None) -> str:
    return DISPLAY_NAMES.get("devices", {}).get(device, "thiết bị")


def room_display_name(room: str | None) -> str:
    return DISPLAY_NAMES.get("rooms", {}).get(room, "phòng")


def supported_room_text(device_registry: dict[str, dict[str, list[str]]]) -> str:
    names = [room_display_name(room) for room in sorted(device_registry.keys())]
    return ", ".join(names)


def supported_device_text(device_registry: dict[str, dict[str, list[str]]]) -> str:
    names = [device_display_name(device) for device in sorted(registry_devices(device_registry))]
    return ", ".join(names)


def validate_mock_slots(
    text: str,
    action: str | None,
    device: str | None,
    room: str | None,
    device_registry: dict[str, dict[str, list[str]]],
) -> dict[str, Any] | None:
    """
    Decide whether mock parser should clarify or create a registry request.

    Rules:
    - User mentioned room-like text, but room is not registered -> registry_request.
    - User mentioned device-like text, but device is not registered -> registry_request.
    - Missing device -> clarify.
    - Missing room -> clarify.
    - Missing action -> clarify.
    """
    if device is None and mentions_device_like(text):
        return make_registry_request_command(
            "Thiết bị này chưa được đăng ký trong hệ thống. "
            "Bạn có muốn gửi yêu cầu thêm thiết bị này vào device_registry không?"
        )

    if room is None and mentions_room_like(text):
        return make_registry_request_command(
            "Phòng này chưa được đăng ký trong hệ thống. "
            "Bạn có muốn gửi yêu cầu thêm phòng này vào device_registry không?"
        )

    if device is None:
        return make_clarify_command(
            response=(
                "Bạn muốn điều khiển thiết bị nào? "
                f"Hiện hệ thống hỗ trợ: {supported_device_text(device_registry)}."
            ),
            action=action,
            device=None,
            room=room,
        )

    if room is None:
        return make_clarify_command(
            response=(
                f"Bạn muốn điều khiển {device_display_name(device)} ở phòng nào? "
                f"Hiện hệ thống hỗ trợ: {supported_room_text(device_registry)}."
            ),
            action=action,
            device=device,
            room=None,
        )

    if action is None:
        return make_clarify_command(
            response=(
                f"Bạn muốn thực hiện hành động nào với "
                f"{device_display_name(device)} ở {room_display_name(room)}?"
            ),
            action=None,
            device=device,
            room=room,
        )

    return None


def determine_next_step(command: dict[str, Any] | None, validation: dict[str, Any]) -> str:
    """
    Decide what the system should do next.

    This function does not execute hardware.
    It only tells CommandService what should happen next.
    """
    if not validation["passed"] or command is None:
        return "stop"

    intent = command.get("intent")

    if intent == "clarify":
        return "clarify"

    if intent == "reject":
        return "reject"

    if intent == "registry_request":
        return "registry_request"

    if intent == "create_rule":
        return "create_rule"

    if command.get("face_auth") is True:
        return "auth_required"

    return "execute"


def mock_parse_command(
    transcript: str,
    device_registry: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """
    Registry-driven mock parser.

    Safety behavior:
    - Missing information -> clarify.
    - Unsupported room/device -> registry_request.
    - No hardware execution when device/room/action is unclear.
    """
    if device_registry is None:
        device_registry = load_device_registry()

    text = transcript.lower().strip()

    if not text:
        raise ValueError("Transcript is empty.")

    if "cái đó" in text or "thiết bị đó" in text:
        return make_clarify_command(
            response="Bạn muốn điều khiển thiết bị nào?",
        )

    device = detect_device(text, device_registry)
    room = "main_door" if device == "door" else detect_room(text, device_registry)
    action = detect_action(text, device)

    precheck_result = validate_mock_slots(
        text=text,
        action=action,
        device=device,
        room=room,
        device_registry=device_registry,
    )
    if precheck_result is not None:
        return precheck_result

    if "nếu" in text and "thì" in text:
        condition = detect_condition(text)

        if condition is None:
            return make_clarify_command(
                response=(
                    "Điều kiện tự động hóa chưa rõ. "
                    "Ví dụ: nếu nhiệt độ trên 30 độ thì bật quạt phòng khách."
                ),
                action=action,
                device=device,
                room=room,
            )

        return {
            "intent": "create_rule",
            "action": action,
            "device": device,
            "room": room,
            "face_auth": False,
            "condition": condition,
            "response": "Đã tạo luật tự động hóa.",
        }

    if action == "get_status":
        return {
            "intent": "query_status",
            "action": "get_status",
            "device": device,
            "room": room,
            "face_auth": False,
            "condition": None,
            "response": "Đang kiểm tra trạng thái thiết bị.",
        }

    if device == "door":
        requires_face_auth = action == "open"

        return {
            "intent": "control_device",
            "action": action,
            "device": "door",
            "room": "main_door",
            "face_auth": requires_face_auth,
            "condition": None,
            "response": (
                "Cần xác thực khuôn mặt trước khi mở cửa."
                if requires_face_auth
                else "Đã xử lý lệnh cửa chính."
            ),
        }

    return {
        "intent": "control_device",
        "action": action,
        "device": device,
        "room": room,
        "face_auth": False,
        "condition": None,
        "response": f"Đã xử lý lệnh điều khiển {device_display_name(device)}.",
    }


def parse_command(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    use_mock: bool = False,
) -> dict[str, Any]:
    if not transcript or not transcript.strip():
        raise ValueError("Transcript is empty.")

    if use_mock or USE_MOCK_LLM:
        return mock_parse_command(transcript, device_registry=device_registry)

    if not GEMINI_API_KEY:
        return mock_parse_command(transcript, device_registry=device_registry)

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = build_prompt(
        transcript=transcript,
        sensor_data=sensor_data,
        device_registry=device_registry,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    raw_text = response.text or ""
    command = extract_json_object(raw_text)

    return normalize_command(command)


def parse_and_validate(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    command_schema: dict[str, Any] | None = None,
    use_mock: bool = False,
) -> dict[str, Any]:
    """
    End-to-end LLM parsing + validation.

    This function does not execute hardware.
    System integration will decide Face Auth, execution, and DB logging.
    """
    start = time.time()

    if device_registry is None:
        device_registry = load_device_registry()

    if command_schema is None:
        command_schema = load_schema()

    try:
        command = parse_command(
            transcript=transcript,
            sensor_data=sensor_data,
            device_registry=device_registry,
            use_mock=use_mock,
        )
        command = normalize_command(command)

        validation = validate_command(
            command=command,
            device_registry=device_registry,
            command_schema=command_schema,
        )

        latency_ms = int((time.time() - start) * 1000)
        next_step = determine_next_step(command, validation)

        return {
            "ok": bool(validation["passed"]),
            "transcript": transcript,
            "command": command,
            "validation": validation,
            "next_step": next_step,
            "latency_ms": latency_ms,
            "log_result": None
            if validation["passed"]
            else validation_code_to_log_result(validation["code"]),
            "error": None if validation["passed"] else validation["message"],
        }

    except Exception as exc:
        latency_ms = int((time.time() - start) * 1000)

        validation = {
            "passed": False,
            "code": "llm_parse_error",
            "message": "LLM failed to return valid JSON.",
        }

        return {
            "ok": False,
            "transcript": transcript,
            "command": None,
            "validation": validation,
            "next_step": "stop",
            "latency_ms": latency_ms,
            "log_result": "error: llm_parse",
            "error": str(exc),
        }


if __name__ == "__main__":
    tests = [
        "bật đèn phòng khách",
        "tắt quạt phòng ngủ",
        "mở cửa chính",
        "đóng cửa chính",
        "quạt phòng ngủ đang thế nào",
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách",
        "bật cái đó lên",
        "bật máy lạnh phòng bếp",
    ]

    for test in tests:
        result = parse_and_validate(test, use_mock=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))