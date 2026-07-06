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
    PROMPT_TEMPLATE_PATH,
    USE_MOCK_LLM,
)
from modules.llm_integration.validator import (
    load_command_schema,
    normalize_command,
    validate_command,
    validation_code_to_log_result,
)

# Từ khóa cấu hình tĩnh cho Mock Parser để code gọn gàng, sạch sẽ hơn
MOCK_ACTIONS = {
    "status": ["trạng thái", "thế nào", "sao rồi", "đang"],
    "open_keywords": ["bật", "mở"],
    "close_keywords": ["tắt", "đóng", "khóa"],
}


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def detect_room(text: str) -> str:
    if "phòng ngủ" in text:
        return "bedroom"

    if "phòng khách" in text:
        return "living_room"

    if "cửa" in text:
        return "main_door"

    return "living_room"


def detect_device(text: str) -> str:
    if "cửa" in text:
        return "door"

    if "quạt" in text:
        return "fan"

    return "light"


def detect_action(text: str, device: str) -> str | None:
    if contains_any(text, MOCK_ACTIONS["status"]):
        return "get_status"

    if device == "door":
        if "mở" in text:
            return "open"

        if contains_any(text, MOCK_ACTIONS["close_keywords"]):
            return "close"

        return None

    if device in {"light", "fan"}:
        if contains_any(text, MOCK_ACTIONS["open_keywords"]):
            return "turn_on"

        if contains_any(text, MOCK_ACTIONS["close_keywords"]):
            return "turn_off"

        return None

    return None


def detect_condition(text: str) -> dict[str, Any] | None:
    """
    Detect simple automation conditions from Vietnamese text.
    MVP support:
    - nhiệt độ trên/lớn hơn/quá 30
    - nhiệt độ dưới/nhỏ hơn 30
    """
    if "nhiệt độ" not in text:
        return None

    number_match = re.search(r"\d+(?:\.\d+)?", text)
    if not number_match:
        return None

    raw_value = number_match.group(0)
    value: int | float
    value = float(raw_value) if "." in raw_value else int(raw_value)

    if any(keyword in text for keyword in ["trên", "lớn hơn", "cao hơn", "quá"]):
        operator = ">"
    elif any(keyword in text for keyword in ["dưới", "nhỏ hơn", "thấp hơn"]):
        operator = "<"
    else:
        operator = ">"

    return {
        "sensor": "temperature",
        "operator": operator,
        "value": value,
    }


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

    if intent == "create_rule":
        return "create_rule"

    if command.get("face_auth") is True:
        return "auth_required"

    return "execute"


def mock_parse_command(transcript: str) -> dict[str, Any]:
    """
    Mock parser for early integration without Gemini API.
    Useful for Flask/System integration tests.
    """
    text = transcript.lower().strip()

    if "cái đó" in text:
        return {
            "intent": "clarify",
            "action": None,
            "device": None,
            "room": None,
            "face_auth": False,
            "condition": None,
            "response": "Bạn muốn bật thiết bị nào?",
        }

    if "máy lạnh" in text or "phòng bếp" in text:
        return {
            "intent": "reject",
            "action": None,
            "device": None,
            "room": None,
            "face_auth": False,
            "condition": None,
            "response": "Thiết bị hoặc phòng này chưa được hỗ trợ trong hệ thống.",
        }

    device = detect_device(text)
    action = detect_action(text, device)
    room = "main_door" if device == "door" else detect_room(text)

    if "nếu" in text and "thì" in text:
        condition = detect_condition(text)

        return {
            "intent": "create_rule",
            "action": action if action else "turn_on",
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
        final_action = action if action else "open"
        requires_face_auth = final_action == "open"

        return {
            "intent": "control_device",
            "action": final_action,
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
        "action": action if action else "turn_on",
        "device": device,
        "room": room,
        "face_auth": False,
        "condition": None,
        "response": f"Đã xử lý lệnh điều khiển {device}.",
    }


def parse_command(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    use_mock: bool = False,
) -> dict[str, Any]:
    if not transcript or not transcript.strip():
        raise ValueError("Transcript is empty.")

    if use_mock or USE_MOCK_LLM:
        return mock_parse_command(transcript)

    if not GEMINI_API_KEY:
        return mock_parse_command(transcript)

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = build_prompt(transcript, sensor_data)

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