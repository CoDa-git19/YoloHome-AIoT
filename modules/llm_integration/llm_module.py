from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from config.settings import (
    COMMAND_SCHEMA_PATH,
    DEVICE_REGISTRY_PATH,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_STRUCTURED_OUTPUT,
    GEMINI_TEMPERATURE,
    GEMINI_THINKING_BUDGET,
    GEMINI_THINKING_LEVEL,
    LANGUAGE_ALIASES_PATH,
    LLM_MAX_RETRIES,
    LLM_RETRY_BACKOFF_SECONDS,
    LLM_TIMEOUT_SECONDS,
    PROMPT_TEMPLATE_PATH,
    USE_MOCK_LLM,
)
from modules.llm_integration.validator import (
    enforce_policy,
    load_command_schema,
    normalize_command,
    validate_command,
    validation_code_to_log_result,
)

logger = logging.getLogger(__name__)


# =============================================================================
# LLM error taxonomy
#
# Phân biệt rõ 3 loại thất bại, vì cách xử lý và ý nghĩa cho dashboard khác nhau:
# - timeout    : model quá chậm / mạng chậm  -> có thể thử lại
# - api_error  : hết quota, sai key, 5xx     -> lỗi hạ tầng
# - parse_error: model trả về không phải JSON -> lỗi chất lượng model
# =============================================================================

class LLMError(Exception):
    """Base error cho mọi thất bại ở tầng LLM."""

    code = "llm_api_error"
    log_result = "error: llm_api"
    user_message = "Hệ thống AI đang gặp sự cố. Vui lòng thử lại."


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    log_result = "error: llm_timeout"
    user_message = "Hệ thống AI phản hồi quá chậm. Vui lòng thử lại."


class LLMAPIError(LLMError):
    code = "llm_api_error"
    log_result = "error: llm_api"
    user_message = "Không kết nối được tới dịch vụ AI. Vui lòng thử lại sau."


class LLMParseError(LLMError):
    code = "llm_parse_error"
    log_result = "error: llm_parse"
    user_message = "Hệ thống chưa hiểu được yêu cầu. Bạn nói lại giúp mình nhé?"


class LLMUnavailableError(LLMError):
    """Model quá tải hoặc lỗi server (5xx). Tạm thời -> nên thử lại."""

    code = "llm_unavailable"
    log_result = "error: llm_unavailable"
    user_message = "Hệ thống AI đang quá tải. Vui lòng thử lại sau ít giây."


class LLMRateLimitError(LLMError):
    """Vượt rate limit (429). Tạm thời -> nên thử lại với backoff."""

    code = "llm_rate_limited"
    log_result = "error: llm_rate_limit"
    user_message = "Hệ thống AI đang bận. Vui lòng thử lại sau ít giây."


# Lỗi TẠM THỜI: thử lại có thể thành công.
RETRYABLE_ERRORS = (LLMTimeoutError, LLMUnavailableError, LLMRateLimitError, LLMParseError)

# Mã HTTP tạm thời -> exception tương ứng.
TRANSIENT_STATUS_CODES = {
    429: LLMRateLimitError,
    500: LLMUnavailableError,
    502: LLMUnavailableError,
    503: LLMUnavailableError,
    504: LLMUnavailableError,
}

# Mã HTTP vĩnh viễn: sai key, sai model, request hỏng. Thử lại chỉ tốn quota.
PERMANENT_STATUS_CODES = {400, 401, 403, 404}


def is_timeout_error(exc: Exception) -> bool:
    """
    Nhận diện timeout mà không cần import cứng httpx / google.genai.errors,
    vì các thư viện này có thể vắng mặt khi chạy ở chế độ mock.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    haystack = f"{name} {text}"

    return (
        "timeout" in haystack
        or "timed out" in haystack
        or "deadline" in haystack          # 504 DEADLINE_EXCEEDED
    )


def extract_status_code(exc: Exception) -> int | None:
    """
    Lấy HTTP status code từ exception của Gemini SDK.

    Ưu tiên thuộc tính .code (SDK mới), fallback về regex trên message
    để không phụ thuộc phiên bản SDK.
    """
    for attribute in ("code", "status_code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value

    match = re.search(r"\b([45]\d{2})\b", str(exc))
    if match:
        return int(match.group(1))

    return None


def extract_retry_delay(exc: Exception) -> float | None:
    """
    Đọc thời gian chờ mà chính nhà cung cấp yêu cầu.

    Gemini trả 429 kèm hướng dẫn rõ ràng:
        "Please retry in 3.407203614s"
        'retryDelay': '3s'

    Backoff mù (0.5s -> 1s -> 2s) sẽ thử lại quá sớm và lại ăn 429 tiếp.
    Nghe theo server bao giờ cũng đúng hơn tự đoán.
    """
    text = str(exc)

    patterns = (
        r"retry in ([\d.]+)\s*s",
        r"retryDelay'?\"?:\s*'?\"?([\d.]+)s",
        r"retry-after[\"':\s]+([\d.]+)",
    )

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue

    return None


def classify_api_error(exc: Exception) -> LLMError:
    """
    Quy một exception thô của SDK về đúng loại LLMError.

    Đây là nơi quyết định lỗi có đáng thử lại hay không, và phải chờ bao lâu.
    """
    error = _classify(exc)
    error.retry_after = extract_retry_delay(exc)

    return error


def _classify(exc: Exception) -> LLMError:
    if is_timeout_error(exc):
        return LLMTimeoutError(f"Gemini request timed out: {exc}")

    status = extract_status_code(exc)

    if status in TRANSIENT_STATUS_CODES:
        error_cls = TRANSIENT_STATUS_CODES[status]
        return error_cls(f"Gemini temporarily unavailable ({status}): {exc}")

    if status in PERMANENT_STATUS_CODES:
        return LLMAPIError(f"Gemini rejected the request ({status}): {exc}")

    return LLMAPIError(f"Gemini API call failed: {exc}")

# Engine selection

def resolve_use_mock(use_mock: bool | None) -> bool:
    """
    Ưu tiên tham số truyền vào; chỉ đọc env khi tham số là None.

    use_mock=True   -> ép dùng mock parser
    use_mock=False  -> ép dùng engine thật (không bị USE_MOCK_LLM ghi đè)
    use_mock=None   -> theo USE_MOCK_LLM trong .env
    """
    if use_mock is None:
        return USE_MOCK_LLM

    return use_mock


def select_engine(use_mock: bool | None) -> str:
    """
    Quyết định engine dùng cho lần parse này: "mock" hoặc "gemini".

    Nếu thiếu GEMINI_API_KEY thì fallback về mock, nhưng phải log warning
    để không bao giờ âm thầm chạy mock trong khi người dùng tưởng là LLM thật.
    """
    if resolve_use_mock(use_mock):
        return "mock"

    if not GEMINI_API_KEY:
        logger.warning(
            "GEMINI_API_KEY is not set. Falling back to mock parser. "
            "Set GEMINI_API_KEY in .env to use the real LLM."
        )
        return "mock"

    return "gemini"

# Config loading

def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_language_aliases() -> dict[str, Any]:
    """Load Vietnamese aliases for mock command parsing."""
    return load_json(LANGUAGE_ALIASES_PATH)


def load_schema() -> dict[str, Any]:
    """Load command schema from config/command_schema.json."""
    return load_command_schema(COMMAND_SCHEMA_PATH)


def load_device_registry() -> dict[str, dict[str, list[str]]]:
    """Load allowed rooms, devices, and actions."""
    return load_json(DEVICE_REGISTRY_PATH)


LANGUAGE_ALIASES = load_language_aliases()


def alias_section(name: str) -> dict[str, Any]:
    """Return a safe alias section from language_aliases.json."""
    value = LANGUAGE_ALIASES.get(name, {})

    if value is None:
        return {}

    if not isinstance(value, dict):
        raise ValueError(f"Alias section '{name}' must be an object.")

    return value


ACTION_ALIASES = alias_section("actions")
ROOM_ALIASES = alias_section("rooms")
DEVICE_ALIASES = alias_section("devices")
DISPLAY_NAMES = alias_section("display_names")
CONDITION_ALIASES = alias_section("conditions")
STATE_NAMES = alias_section("state_names")
RULE_TRIGGERS = alias_section("rule_triggers")


def alias_list(
    alias_group: dict[str, Any],
    key: str,
    default: list[str] | None = None,
) -> list[str]:
    """Return a safe alias list from config."""
    value = alias_group.get(key, default or [])

    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [str(item) for item in value]

    raise ValueError(f"Alias config for '{key}' must be a list or string.")

# Registry helpers

def registry_rooms(device_registry: dict[str, dict[str, list[str]]]) -> set[str]:
    return set(device_registry.keys())


def registry_devices(device_registry: dict[str, dict[str, list[str]]]) -> set[str]:
    devices: set[str] = set()

    for room_devices in device_registry.values():
        devices.update(room_devices.keys())

    return devices


def rooms_for_device(
    device: str,
    device_registry: dict[str, dict[str, list[str]]],
) -> list[str]:
    """Return every registered room that contains the given device."""
    return [
        room
        for room, room_devices in device_registry.items()
        if device in room_devices
    ]


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)

# Display helpers

def device_display_name(device: str | None) -> str:
    return DISPLAY_NAMES.get("devices", {}).get(device, "thiết bị")


def room_display_name(room: str | None) -> str:
    return DISPLAY_NAMES.get("rooms", {}).get(room, "phòng")


def state_display_name(state: str | None) -> str:
    """
    Đổi trạng thái phần cứng ("on"/"off"/"open"/"closed") sang tiếng Việt.

    Từ điển nằm trong config/language_aliases.json -> state_names,
    nên thêm trạng thái mới không phải sửa code.
    """
    if not state:
        return STATE_NAMES.get("unknown", "không xác định được trạng thái")

    return STATE_NAMES.get(state, STATE_NAMES.get("unknown", "không xác định"))


def supported_room_text(device_registry: dict[str, dict[str, list[str]]]) -> str:
    names = [room_display_name(room) for room in sorted(device_registry.keys())]
    return ", ".join(names)


def supported_device_text(device_registry: dict[str, dict[str, list[str]]]) -> str:
    names = [
        device_display_name(device)
        for device in sorted(registry_devices(device_registry))
    ]
    return ", ".join(names)

# Prompt building (Gemini)

def format_sensitive_actions(command_schema: dict[str, Any]) -> str:
    """Liệt kê hành động nhạy cảm cho prompt, lấy từ command_schema.json."""
    lines = [
        f"  - {item.get('device')}.{item.get('action')}"
        for item in command_schema.get("sensitive_actions", [])
        if item.get("face_auth") is True
    ]

    return "\n".join(lines) if lines else "  (không có)"


def build_prompt(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    device_registry: dict[str, Any] | None = None,
    pending_command: dict[str, Any] | None = None,
    command_schema: dict[str, Any] | None = None,
) -> str:
    """
    Build Gemini prompt từ template + config.

    Intents, sensors, operators và sensitive_actions đều lấy từ
    command_schema.json, KHÔNG hardcode trong prompt. Trước kia prompt và
    command_schema là 2 nguồn sự thật song song và rất dễ lệch nhau.
    """
    if device_registry is None:
        device_registry = load_device_registry()

    if command_schema is None:
        command_schema = load_schema()

    if sensor_data is None:
        sensor_data = {}

    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    return (
        template.replace(
            "{valid_intents}",
            " | ".join(command_schema.get("intents", [])),
        )
        .replace(
            "{valid_sensors}",
            " | ".join(command_schema.get("valid_sensors", [])),
        )
        .replace(
            "{valid_operators}",
            " | ".join(command_schema.get("valid_operators", [])),
        )
        .replace(
            "{sensitive_actions}",
            format_sensitive_actions(command_schema),
        )
        .replace(
            "{device_registry}",
            json.dumps(device_registry, ensure_ascii=False, indent=2),
        )
        .replace(
            "{sensor_data}",
            json.dumps(sensor_data, ensure_ascii=False, indent=2),
        )
        .replace(
            "{pending_command}",
            json.dumps(pending_command, ensure_ascii=False, indent=2)
            if pending_command
            else "null",
        )
        .replace("{transcript}", transcript)
    )


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Trích JSON object ĐẦU TIÊN từ câu trả lời của model.

    Dùng raw_decode thay vì regex tham lam: regex kiểu "{ bất kỳ }" sẽ nuốt
    từ dấu ngoặc nhọn MỞ đầu tiên tới dấu ĐÓNG cuối cùng, nên vỡ ngay khi
    model kèm thêm văn bản hoặc dấu ngoặc nhọn phía sau.
    """
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()

    # Quét từng vị trí "{" và thử decode. raw_decode dừng đúng chỗ object
    # kết thúc, không quan tâm phía sau còn gì.
    for index, char in enumerate(cleaned):
        if char != "{":
            continue

        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict):
            return parsed

    raise ValueError("No JSON object found in LLM response.")

# Mock parser: slot detection

def best_alias_match(text: str, aliases: list[str]) -> str | None:
    """
    Trả về alias DÀI NHẤT khớp trong text (longest-match).

    Longest-match tránh việc alias ngắn nuốt alias dài, ví dụ
    "rèm cửa" không bị nhận nhầm thành "cửa".
    """
    matched = [alias for alias in aliases if alias in text]

    if not matched:
        return None

    return max(matched, key=len)


def detect_device(
    text: str,
    device_registry: dict[str, dict[str, list[str]]],
) -> str | None:
    """Detect a supported device from transcript based on device_registry."""
    best_device: str | None = None
    best_len = 0

    for device_key in registry_devices(device_registry):
        aliases = alias_list(DEVICE_ALIASES, device_key, [device_key])
        match = best_alias_match(text, aliases)

        if match is not None and len(match) > best_len:
            best_device = device_key
            best_len = len(match)

    return best_device


def detect_room(
    text: str,
    device_registry: dict[str, dict[str, list[str]]],
) -> str | None:
    """Detect a supported room from transcript based on device_registry."""
    best_room: str | None = None
    best_len = 0

    for room_key in registry_rooms(device_registry):
        aliases = alias_list(ROOM_ALIASES, room_key, [room_key])
        match = best_alias_match(text, aliases)

        if match is not None and len(match) > best_len:
            best_room = room_key
            best_len = len(match)

    return best_room


def resolve_room(
    text: str,
    device: str | None,
    device_registry: dict[str, dict[str, list[str]]],
) -> str | None:
    """
    Resolve the target room, registry-driven.

    1. If the transcript names a registered room, use it.
    2. Otherwise, if the device exists in exactly one registered room, infer it.
    3. Otherwise return None so the caller can ask for clarification.

    Thay cho luật hardcode cũ: room = "main_door" if device == "door".
    """
    room = detect_room(text, device_registry)

    if room is not None:
        return room

    if device is None:
        return None

    candidate_rooms = rooms_for_device(device, device_registry)

    if len(candidate_rooms) == 1:
        return candidate_rooms[0]

    return None


def mentions_room_like(text: str) -> bool:
    """
    Detect whether user seems to mention a room/location.

    Dùng để phân biệt:
    - thiếu room   -> clarify
    - room lạ      -> registry_request
    """
    for aliases in ROOM_ALIASES.values():
        if best_alias_match(text, aliases):
            return True

    return "phòng" in text


def mentions_device_like(text: str) -> bool:
    """
    Detect whether user seems to mention a device.

    Dùng toàn bộ alias đã biết, kể cả device chưa có trong registry.
    Registry vẫn là nơi quyết định device có được hỗ trợ hay không.
    """
    for aliases in DEVICE_ALIASES.values():
        if best_alias_match(text, aliases):
            return True

    return False


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


def find_alias(text: str, aliases: list[str]) -> tuple[str, int, int] | None:
    """
    Tìm alias DÀI NHẤT khớp trong text, theo RANH GIỚI TỪ.

    Ranh giới từ rất quan trọng với tiếng Việt: alias "là" không được phép
    khớp vào giữa chữ "làm" (trong "phòng làm việc").

    Trả về (alias, start, end) hoặc None.
    """
    best: tuple[str, int, int] | None = None

    for alias in aliases:
        match = re.search(
            rf"(?<!\w){re.escape(alias)}(?!\w)",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue

        if best is None or len(alias) > len(best[0]):
            best = (alias, match.start(), match.end())

    return best


def parse_number(raw: str) -> int | float:
    """Đọc số, chấp nhận cả dấu phẩy thập phân kiểu Việt Nam (30,5)."""
    normalized = raw.replace(",", ".")

    if "." in normalized:
        return float(normalized)

    return int(normalized)


def detect_operator(segment: str) -> str | None:
    """
    Tìm toán tử trong ĐOẠN VĂN giữa cảm biến và con số.

    Giới hạn phạm vi tìm kiếm tránh việc alias của toán tử vô tình khớp vào
    phần khác của câu (ví dụ "là" trong "phòng làm việc").
    """
    operator_aliases = CONDITION_ALIASES.get("operators", {})

    best_operator: str | None = None
    best_length = 0

    for operator_key, aliases in operator_aliases.items():
        found = find_alias(segment, aliases)

        if found is not None and len(found[0]) > best_length:
            best_operator = operator_key
            best_length = len(found[0])

    return best_operator


def is_rule_statement(text: str) -> bool:
    """
    Câu này có phải đang mô tả một luật tự động hoá không?

    Từ khoá nằm trong config/language_aliases.json -> rule_triggers, nên
    thêm cách nói mới ("hễ", "mỗi khi"...) không phải sửa code.
    """
    if_words = alias_list(RULE_TRIGGERS, "if", ["nếu"])
    then_words = alias_list(RULE_TRIGGERS, "then", ["thì"])

    has_if = find_alias(text, if_words) is not None
    has_then = find_alias(text, then_words) is not None

    return has_if and has_then


def detect_condition(text: str) -> dict[str, Any] | None:
    """
    Nhận diện điều kiện automation từ câu tiếng Việt.

    Con số phải nằm SAU tên cảm biến, và toán tử phải nằm GIỮA cảm biến và
    con số. Nếu không, câu "sau 5 phút nếu nhiệt độ trên 30 thì bật quạt"
    sẽ lấy nhầm số 5 làm ngưỡng.
    """
    sensor_aliases = CONDITION_ALIASES.get("sensors", {})

    # 1. Cảm biến: lấy alias dài nhất, ghi lại vị trí kết thúc.
    sensor: str | None = None
    sensor_end = 0
    best_length = 0

    for sensor_key, aliases in sensor_aliases.items():
        found = find_alias(text, aliases)

        if found is not None and len(found[0]) > best_length:
            sensor = sensor_key
            sensor_end = found[2]
            best_length = len(found[0])

    if sensor is None:
        return None

    # 2. Con số: phải nằm SAU tên cảm biến.
    tail = text[sensor_end:]
    number_match = re.search(r"\d+(?:[.,]\d+)?", tail)

    if not number_match:
        return None

    value = parse_number(number_match.group(0))

    # 3. Toán tử: chỉ tìm trong đoạn giữa cảm biến và con số.
    segment = tail[: number_match.start()]
    operator = detect_operator(segment) or ">"

    return {
        "sensor": sensor,
        "operator": operator,
        "value": value,
    }

# Command factories

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


def missing_slot_question(
    command: dict[str, Any],
    device_registry: dict[str, dict[str, list[str]]],
) -> str:
    """
    Sinh câu hỏi lại bằng tiếng Việt cho command bị thiếu slot.

    Dùng khi LLM trả về command không có device/room/action.
    """
    device = command.get("device")
    room = command.get("room")

    if device is None:
        return (
            "Bạn muốn điều khiển thiết bị nào? "
            f"Hiện hệ thống hỗ trợ: {supported_device_text(device_registry)}."
        )

    if room is None:
        return (
            f"Bạn muốn điều khiển {device_display_name(device)} ở phòng nào? "
            f"Hiện hệ thống hỗ trợ: {supported_room_text(device_registry)}."
        )

    return (
        "Bạn muốn thực hiện hành động nào với "
        f"{device_display_name(device)} ở {room_display_name(room)}?"
    )


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
    - Nhắc tới device nhưng device chưa đăng ký -> registry_request.
    - Nhắc tới room nhưng room chưa đăng ký     -> registry_request.
    - Thiếu device / room / action              -> clarify.
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
                "Bạn muốn thực hiện hành động nào với "
                f"{device_display_name(device)} ở {room_display_name(room)}?"
            ),
            action=None,
            device=device,
            room=room,
        )

    return None

# Multi-turn slot filling
#
# Bot hỏi "đèn ở phòng nào?" thì phải NGHE được câu trả lời "phòng khách".
# Không có phần này, clarify là ngõ cụt: bot hỏi rồi quên sạch, hỏi lại từ đầu.

SLOT_FIELDS = ("action", "device", "room", "condition")


def is_topic_change(command: dict[str, Any]) -> bool:
    """
    Câu mới có phải một lệnh độc lập không (user đổi ý), thay vì đang trả lời
    câu hỏi làm rõ trước đó?

    Nếu câu mới đã tự đủ device + room + action thì coi như lệnh mới.
    """
    if command.get("intent") in {"reject", "registry_request"}:
        return True

    return all(
        command.get(field) is not None for field in ("action", "device", "room")
    )


def resolve_intent_after_merge(command: dict[str, Any]) -> dict[str, Any]:
    """
    Sau khi ghép đủ slot, command không còn là "clarify" nữa.

    Suy lại intent thật từ nội dung, vì intent gốc đã mất khi parser
    trả về clarify.
    """
    resolved = dict(command)

    if resolved.get("intent") != "clarify":
        return resolved

    slots_complete = all(
        resolved.get(field) is not None for field in ("action", "device", "room")
    )
    if not slots_complete:
        return resolved

    if resolved.get("condition") is not None:
        resolved["intent"] = "create_rule"
    elif resolved.get("action") == "get_status":
        resolved["intent"] = "query_status"
    else:
        resolved["intent"] = "control_device"

    # Câu hỏi cũ ("Bạn muốn ... ở phòng nào?") không còn hợp lý nữa.
    resolved["face_auth"] = (
        resolved.get("device") == "door" and resolved.get("action") == "open"
    )
    resolved["response"] = default_response_for(resolved)

    return resolved


def default_response_for(command: dict[str, Any]) -> str:
    """Câu trả lời mặc định cho một command đã đủ slot."""
    intent = command.get("intent")
    device = command.get("device")
    action = command.get("action")

    if intent == "create_rule":
        return "Đã tạo luật tự động hóa."

    if intent == "query_status":
        return "Đang kiểm tra trạng thái thiết bị."

    if device == "door":
        if action == "open":
            return "Cần xác thực khuôn mặt trước khi mở cửa."
        return "Đã xử lý lệnh cửa chính."

    return f"Đã xử lý lệnh điều khiển {device_display_name(device)}."


def merge_with_pending(
    command: dict[str, Any],
    pending: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Ghép câu trả lời mới vào command đang chờ làm rõ.

    - Slot câu mới KHÔNG nói tới  -> lấy từ command cũ.
    - Slot câu mới CÓ nói tới     -> giữ nguyên (user được quyền đổi ý).
    """
    if not pending:
        return command

    merged = dict(command)

    for field in SLOT_FIELDS:
        if merged.get(field) is None and pending.get(field) is not None:
            merged[field] = pending[field]

    return resolve_intent_after_merge(merged)


# Routing

def determine_next_step(
    command: dict[str, Any] | None,
    validation: dict[str, Any],
) -> str:
    """
    Decide what the system should do next.

    This function does not execute hardware.
    It only tells CommandService what should happen next.
    """
    if command is None:
        return "stop"

    # Thiếu slot không phải lỗi hệ thống: hỏi lại người dùng thay vì dừng.
    if validation.get("code") == "missing_slot":
        return "clarify"

    if not validation["passed"]:
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

# Parsers

def mock_parse_command(
    transcript: str,
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    pending_command: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Registry-driven mock parser.

    Safety behavior:
    - Thiếu thông tin           -> clarify.
    - Room/device chưa hỗ trợ   -> registry_request.
    - Không execute phần cứng khi device/room/action chưa rõ.
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
    room = resolve_room(text, device, device_registry)

    # Multi-turn: "bật lên" một mình không có device, nên detect_action() bó tay.
    # Dùng device từ lượt trước làm gợi ý để hiểu được "bật" là turn_on hay open.
    device_hint = device or (pending_command or {}).get("device")
    action = detect_action(text, device_hint)

    precheck_result = validate_mock_slots(
        text=text,
        action=action,
        device=device,
        room=room,
        device_registry=device_registry,
    )
    if precheck_result is not None:
        return precheck_result

    if is_rule_statement(text):
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
            "room": room,
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


def build_response_schema(
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    command_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Sinh response_schema cho Gemini structured output, từ config chứ không hardcode.

    Nhờ enum lấy từ device_registry, model bị RÀNG BUỘC chỉ được trả về
    room/device/action có thật trong hệ thống - thay vì hy vọng prompt đủ mạnh.
    """
    if device_registry is None:
        device_registry = load_device_registry()

    if command_schema is None:
        command_schema = load_schema()

    actions: set[str] = set()
    for room_devices in device_registry.values():
        for allowed_actions in room_devices.values():
            actions.update(allowed_actions)

    return {
        "type": "OBJECT",
        "properties": {
            "intent": {
                "type": "STRING",
                "enum": sorted(command_schema.get("intents", [])),
            },
            "action": {
                "type": "STRING",
                "enum": sorted(actions),
                "nullable": True,
            },
            "device": {
                "type": "STRING",
                "enum": sorted(registry_devices(device_registry)),
                "nullable": True,
            },
            "room": {
                "type": "STRING",
                "enum": sorted(registry_rooms(device_registry)),
                "nullable": True,
            },
            "face_auth": {"type": "BOOLEAN"},
            "condition": {
                "type": "OBJECT",
                "nullable": True,
                "properties": {
                    "sensor": {
                        "type": "STRING",
                        "enum": sorted(command_schema.get("valid_sensors", [])),
                    },
                    "operator": {
                        "type": "STRING",
                        "enum": sorted(command_schema.get("valid_operators", [])),
                    },
                    "value": {"type": "NUMBER"},
                },
                "required": ["sensor", "operator", "value"],
            },
            "response": {"type": "STRING"},
        },
        "required": sorted(command_schema.get("required_fields", [])),
    }


def build_thinking_config(types: Any) -> Any | None:
    """
    Tắt/giảm thinking để cắt latency.

    Parse "bật đèn phòng khách" thành JSON không cần suy luận sâu. Thinking mặc
    định (medium trên Gemini 3.x) đẩy latency lên hàng chục giây và gây
    504 DEADLINE_EXCEEDED.

    SDK/model khác nhau dùng tham số khác nhau, nên thử lần lượt:
    - thinking_level  (Gemini 3.x)
    - thinking_budget (Gemini 2.5)
    """
    thinking_config = getattr(types, "ThinkingConfig", None)
    if thinking_config is None:
        return None

    if GEMINI_THINKING_LEVEL:
        try:
            return thinking_config(thinking_level=GEMINI_THINKING_LEVEL)
        except TypeError:
            pass  # SDK cũ chưa có thinking_level -> thử thinking_budget

    if GEMINI_THINKING_BUDGET:
        try:
            return thinking_config(thinking_budget=int(GEMINI_THINKING_BUDGET))
        except (TypeError, ValueError):
            pass

    return None


def build_gemini_config(
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    command_schema: dict[str, Any] | None = None,
    structured_output: bool | None = None,
    thinking: bool = True,
) -> Any:
    """Build GenerateContentConfig: temperature, timeout, structured output, thinking."""
    from google.genai import types

    if structured_output is None:
        structured_output = GEMINI_STRUCTURED_OUTPUT

    config: dict[str, Any] = {
        "temperature": GEMINI_TEMPERATURE,
        "http_options": types.HttpOptions(
            timeout=int(LLM_TIMEOUT_SECONDS * 1000),  # SDK dùng milliseconds
        ),
    }

    if structured_output:
        config["response_mime_type"] = "application/json"
        config["response_schema"] = build_response_schema(
            device_registry=device_registry,
            command_schema=command_schema,
        )

    if thinking:
        thinking_config = build_thinking_config(types)
        if thinking_config is not None:
            config["thinking_config"] = thinking_config

    return types.GenerateContentConfig(**config)


def call_gemini(prompt: str, config: Any, model: str | None = None) -> str:
    """
    Gọi Gemini một lần và trả về raw text.

    Mọi lỗi hạ tầng được quy về LLMTimeoutError / LLMAPIError để tầng trên
    phân biệt được, thay vì gộp tất cả thành "LLM failed to return valid JSON".
    """
    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        response = client.models.generate_content(
            model=model or GEMINI_MODEL,
            contents=prompt,
            config=config,
        )
    except Exception as exc:
        raise classify_api_error(exc) from exc

    return response.text or ""


def retry_delay_seconds(last_error: LLMError | None, attempt: int) -> float:
    """
    Chờ bao lâu trước lần thử thứ `attempt`.

    Ưu tiên thời gian mà NHÀ CUNG CẤP yêu cầu (Gemini trả retryDelay trong
    lỗi 429). Chỉ khi không có mới dùng exponential backoff tự đoán:
    0.5s -> 1s -> 2s.

    Backoff mù thử lại quá sớm sẽ ăn 429 tiếp và đốt quota vô ích.
    """
    backoff = LLM_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 2))

    requested = getattr(last_error, "retry_after", None) if last_error else None

    if requested is None:
        logger.info("Retrying Gemini in %.2fs (attempt %d).", backoff, attempt)
        return backoff

    # Cộng thêm một chút cho chắc: server nói 3.4s thì chờ hơn 3.4s.
    delay = max(backoff, requested + 0.25)

    logger.info(
        "Nhà cung cấp yêu cầu chờ %.2fs. Thử lại sau %.2fs (lần %d).",
        requested,
        delay,
        attempt,
    )

    return delay


def gemini_parse_command(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    command_schema: dict[str, Any] | None = None,
    pending_command: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """
    Call Gemini and extract a normalized command object.

    Retry policy:
    - Lỗi parse JSON  -> thử lại (kèm nhắc nhở "JSON only"), tối đa LLM_MAX_RETRIES lần.
    - Timeout         -> thử lại.
    - Lỗi API (quota, sai key, 5xx) -> KHÔNG thử lại, vì thử lại cũng vô ích.
    """
    prompt = build_prompt(
        transcript=transcript,
        sensor_data=sensor_data,
        device_registry=device_registry,
        pending_command=pending_command,
        command_schema=command_schema,
    )

    # Các mức config, thử từ đầy đủ nhất xuống đơn giản nhất.
    # Nếu SDK/model không chấp nhận thinking_config hoặc response_schema,
    # Gemini trả 400 INVALID_ARGUMENT -> tự hạ cấp thay vì chết hẳn.
    config_levels = [
        {"structured_output": True, "thinking": True},
        {"structured_output": True, "thinking": False},
        {"structured_output": False, "thinking": False},
    ]
    config_level = 0

    def current_config() -> Any:
        return build_gemini_config(
            device_registry=device_registry,
            command_schema=command_schema,
            **config_levels[config_level],
        )

    config = current_config()

    total_attempts = max(1, LLM_MAX_RETRIES + 1)
    last_error: LLMError | None = None

    for attempt in range(1, total_attempts + 1):
        if attempt > 1:
            time.sleep(retry_delay_seconds(last_error, attempt))

        try:
            raw_text = call_gemini(prompt, config, model=model)
        except LLMAPIError as exc:
            # 400 INVALID_ARGUMENT thường do config chứa tính năng model không
            # hỗ trợ. Hạ cấp config rồi thử lại, thay vì bỏ cuộc.
            if (
                extract_status_code(exc) == 400
                and config_level < len(config_levels) - 1
            ):
                config_level += 1
                config = current_config()
                logger.warning(
                    "Gemini rejected the request config. Degrading to level %d "
                    "(structured_output=%s, thinking=%s) and retrying: %s",
                    config_level,
                    config_levels[config_level]["structured_output"],
                    config_levels[config_level]["thinking"],
                    exc,
                )
                last_error = exc
                continue

            # Sai key / sai model: thử lại chỉ tốn quota.
            raise exc
        except RETRYABLE_ERRORS as exc:
            last_error = exc
            logger.warning(
                "Gemini call failed (%s, attempt %d/%d): %s",
                exc.code,
                attempt,
                total_attempts,
                exc,
            )
            continue

        try:
            command = extract_json_object(raw_text)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = LLMParseError(f"Gemini did not return valid JSON: {exc}")
            logger.warning(
                "Gemini returned non-JSON (attempt %d/%d): %r",
                attempt,
                total_attempts,
                raw_text[:200],
            )
            prompt = (
                f"{prompt}\n\n"
                "LƯU Ý: Lần trước bạn trả về sai định dạng. "
                "Chỉ trả về DUY NHẤT một JSON object, không markdown, không giải thích."
            )
            continue

        if attempt > 1:
            logger.info("Gemini succeeded on retry attempt %d.", attempt)

        return normalize_command(command, command_schema=command_schema)

    raise last_error or LLMParseError("Gemini failed to return a valid command.")


def parse_command(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    command_schema: dict[str, Any] | None = None,
    pending_command: dict[str, Any] | None = None,
    model: str | None = None,
    use_mock: bool | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    """
    Parse a transcript into a raw command object.

    engine:
    - None     -> tự resolve từ use_mock / env qua select_engine()
    - "mock"   -> registry-driven mock parser
    - "gemini" -> Gemini API
    """
    if not transcript or not transcript.strip():
        raise ValueError("Transcript is empty.")

    if engine is None:
        engine = select_engine(use_mock)

    if engine == "mock":
        return mock_parse_command(
            transcript,
            device_registry=device_registry,
            pending_command=pending_command,
        )

    return gemini_parse_command(
        transcript=transcript,
        sensor_data=sensor_data,
        device_registry=device_registry,
        command_schema=command_schema,
        pending_command=pending_command,
        model=model,
    )


def parse_and_validate(
    transcript: str,
    sensor_data: dict[str, Any] | None = None,
    device_registry: dict[str, dict[str, list[str]]] | None = None,
    command_schema: dict[str, Any] | None = None,
    pending_command: dict[str, Any] | None = None,
    model: str | None = None,
    use_mock: bool | None = None,
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

    engine = select_engine(use_mock)

    try:
        command = parse_command(
            transcript=transcript,
            sensor_data=sensor_data,
            device_registry=device_registry,
            command_schema=command_schema,
            pending_command=pending_command,
            model=model,
            engine=engine,
        )
        command = normalize_command(command, command_schema=command_schema)

        # Multi-turn: người dùng đang trả lời câu hỏi làm rõ trước đó.
        # Ghép slot mới vào command cũ, trừ khi họ đã đổi hẳn sang lệnh khác.
        if pending_command and not is_topic_change(command):
            command = merge_with_pending(command, pending_command)
            command = normalize_command(command, command_schema=command_schema)

            # Vẫn thiếu slot -> hỏi lại câu hỏi ĐÚNG với những gì đã biết,
            # không lặp lại câu hỏi cũ ("thiết bị nào?" khi đã biết là quạt).
            if command.get("intent") == "clarify":
                command["response"] = missing_slot_question(command, device_registry)

        # Server-side security enforcement: LLM không được quyết định face_auth.
        command, policy_overrides = enforce_policy(
            command,
            command_schema=command_schema,
        )

        validation = validate_command(
            command=command,
            device_registry=device_registry,
            command_schema=command_schema,
        )

        next_step = determine_next_step(command, validation)

        # LLM trả command thiếu slot -> chuyển sang clarify kèm câu hỏi cụ thể,
        # thay vì báo "lỗi hệ thống" cho người dùng.
        if validation.get("code") == "missing_slot":
            command = make_clarify_command(
                response=missing_slot_question(command, device_registry),
                action=command.get("action"),
                device=command.get("device"),
                room=command.get("room"),
            )

        latency_ms = int((time.time() - start) * 1000)

        return {
            "ok": bool(validation["passed"]),
            "engine": engine,
            "model": (model or GEMINI_MODEL) if engine == "gemini" else "mock",
            "transcript": transcript,
            "command": command,
            "validation": validation,
            "policy_overrides": policy_overrides,
            "next_step": next_step,
            "latency_ms": latency_ms,
            "log_result": None
            if validation["passed"]
            else validation_code_to_log_result(validation["code"]),
            "error": None if validation["passed"] else validation["message"],
        }

    except LLMError as exc:
        # Lỗi tầng LLM đã được phân loại sẵn: timeout / api_error / parse_error.
        latency_ms = int((time.time() - start) * 1000)
        logger.error("LLM failure (%s): %s", exc.code, exc)

        return {
            "ok": False,
            "engine": engine,
            "model": (model or GEMINI_MODEL) if engine == "gemini" else "mock",
            "transcript": transcript,
            "command": None,
            "validation": {
                "passed": False,
                "code": exc.code,
                "message": exc.user_message,
            },
            "policy_overrides": [],
            "next_step": "stop",
            "latency_ms": latency_ms,
            "log_result": exc.log_result,
            "error": str(exc),
        }

    except Exception as exc:
        # Lỗi ngoài dự kiến (transcript rỗng, config hỏng, bug...).
        latency_ms = int((time.time() - start) * 1000)
        logger.exception("Unexpected failure while parsing transcript.")

        return {
            "ok": False,
            "engine": engine,
            "model": (model or GEMINI_MODEL) if engine == "gemini" else "mock",
            "transcript": transcript,
            "command": None,
            "validation": {
                "passed": False,
                "code": "llm_parse_error",
                "message": "Hệ thống chưa hiểu được yêu cầu. Bạn nói lại giúp mình nhé?",
            },
            "policy_overrides": [],
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
        "mở rèm cửa phòng khách",
        "bật đèn phòng khách đang tắt",
    ]

    for test in tests:
        result = parse_and_validate(test, use_mock=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))