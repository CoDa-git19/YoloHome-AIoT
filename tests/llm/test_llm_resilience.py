"""
Tests cho độ bền của tầng LLM (P1-5).

Kiểm chứng:
- response_schema gửi lên Gemini được sinh TỪ CONFIG (device_registry, command_schema),
  không hardcode -> thêm thiết bị mới không phải sửa code.
- Retry khi model trả sai định dạng hoặc timeout.
- KHÔNG retry khi lỗi API (hết quota, sai key) vì thử lại cũng vô ích.
- Phân loại lỗi: llm_timeout / llm_api_error / llm_parse_error, thay vì gộp
  tất cả thành "LLM failed to return valid JSON".

Gemini SDK được giả lập, nên test chạy offline và không tốn quota.
"""

from __future__ import annotations

import sys
import types as pytypes
from typing import Any

import pytest

import modules.llm_integration.llm_module as llm_module
from modules.llm_integration.llm_module import (
    build_response_schema,
    classify_api_error,
    extract_status_code,
    is_timeout_error,
    parse_and_validate,
)


VALID_JSON = (
    '{"intent":"control_device","action":"turn_on","device":"fan",'
    '"room":"bedroom","face_auth":false,"condition":null,'
    '"response":"Đã bật quạt phòng ngủ."}'
)


class FakeTimeoutError(Exception):
    """Tên class chứa 'Timeout' -> is_timeout_error() phải nhận ra."""


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, calls: list[dict[str, Any]], script: list[Any]) -> None:
        self.calls = calls
        self.script = script

    def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "prompt": contents, "config": config})

        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

        return _FakeResponse(outcome)


@pytest.fixture
def fake_gemini(monkeypatch):
    """
    Cài một google.genai giả vào sys.modules và bật engine gemini.

    Trả về hàm run(script) -> (result, calls).
    """
    calls: list[dict[str, Any]] = []
    script: list[Any] = []

    class HttpOptions:
        def __init__(self, timeout=None):
            self.timeout = timeout

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ThinkingConfig:
        def __init__(self, thinking_level=None, thinking_budget=None):
            self.thinking_level = thinking_level
            self.thinking_budget = thinking_budget

    class Client:
        def __init__(self, api_key=None):
            self.models = _FakeModels(calls, script)

    google = pytypes.ModuleType("google")
    genai = pytypes.ModuleType("google.genai")
    gtypes = pytypes.ModuleType("google.genai.types")

    gtypes.HttpOptions = HttpOptions
    gtypes.GenerateContentConfig = GenerateContentConfig
    gtypes.ThinkingConfig = ThinkingConfig
    genai.Client = Client
    genai.types = gtypes
    google.genai = genai

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", gtypes)

    # Ép select_engine() -> "gemini"
    monkeypatch.setattr(llm_module, "GEMINI_API_KEY", "fake-key")

    # Tắt backoff để test không phải sleep thật.
    monkeypatch.setattr(llm_module, "LLM_RETRY_BACKOFF_SECONDS", 0.0)

    def run(outcomes: list[Any], transcript: str = "bật quạt phòng ngủ"):
        calls.clear()
        script.clear()
        script.extend(outcomes)

        result = parse_and_validate(transcript, use_mock=False)
        return result, calls

    return run


# =============================================================================
# 1. response_schema sinh từ config
# =============================================================================

def test_response_schema_enums_come_from_device_registry():
    schema = build_response_schema()

    assert schema["properties"]["device"]["enum"] == ["door", "fan", "light"]
    assert schema["properties"]["room"]["enum"] == [
        "bedroom",
        "living_room",
        "main_door",
    ]
    assert "turn_on" in schema["properties"]["action"]["enum"]


def test_response_schema_enums_come_from_command_schema():
    schema = build_response_schema()

    assert "control_device" in schema["properties"]["intent"]["enum"]
    assert "clarify" in schema["properties"]["intent"]["enum"]

    condition = schema["properties"]["condition"]["properties"]
    assert "temperature" in condition["sensor"]["enum"]
    assert ">" in condition["operator"]["enum"]


def test_response_schema_allows_null_slots():
    """Intent clarify/reject có action/device/room = null, nên phải nullable."""
    schema = build_response_schema()

    for field in ("action", "device", "room", "condition"):
        assert schema["properties"][field]["nullable"] is True


def test_gemini_config_sets_temperature_timeout_and_structured_output(fake_gemini):
    _, calls = fake_gemini([VALID_JSON])
    config = calls[0]["config"]

    assert config.temperature == 0
    assert config.http_options.timeout == 15000  # 15s -> milliseconds
    assert config.response_mime_type == "application/json"
    assert config.response_schema["properties"]["intent"]["enum"]


def test_gemini_config_disables_thinking_to_cut_latency(fake_gemini):
    """
    Parse câu lệnh ngắn không cần suy luận sâu. Thinking mặc định đẩy latency
    lên hàng chục giây và gây 504 DEADLINE_EXCEEDED.
    """
    _, calls = fake_gemini([VALID_JSON])
    config = calls[0]["config"]

    assert config.thinking_config.thinking_level == "minimal"


# =============================================================================
# 2. Đường thành công
# =============================================================================

def test_valid_json_succeeds_on_first_call(fake_gemini):
    result, calls = fake_gemini([VALID_JSON])

    assert len(calls) == 1
    assert result["engine"] == "gemini"
    assert result["next_step"] == "execute"
    assert result["command"]["device"] == "fan"


# =============================================================================
# 3. Retry
# =============================================================================

def test_retries_when_model_returns_non_json(fake_gemini):
    result, calls = fake_gemini(["Xin lỗi, tôi không hiểu.", VALID_JSON])

    assert len(calls) == 2
    assert result["next_step"] == "execute"


def test_retry_prompt_includes_repair_hint(fake_gemini):
    _, calls = fake_gemini(["không phải json", VALID_JSON])

    assert "LƯU Ý" not in calls[0]["prompt"]
    assert "LƯU Ý" in calls[1]["prompt"]


def test_gives_up_with_parse_error_after_retries(fake_gemini):
    result, calls = fake_gemini(["rác 1", "rác 2", "rác 3"])

    assert len(calls) == 3  # 1 lần đầu + LLM_MAX_RETRIES(2)
    assert result["ok"] is False
    assert result["next_step"] == "stop"
    assert result["validation"]["code"] == "llm_parse_error"
    assert result["log_result"] == "error: llm_parse"


def test_retries_on_timeout(fake_gemini):
    result, calls = fake_gemini([FakeTimeoutError("read timeout"), VALID_JSON])

    assert len(calls) == 2
    assert result["next_step"] == "execute"


def test_timeout_error_is_classified(fake_gemini):
    result, calls = fake_gemini([FakeTimeoutError("read timeout")] * 3)

    assert len(calls) == 3
    assert result["validation"]["code"] == "llm_timeout"
    assert result["log_result"] == "error: llm_timeout"


# =============================================================================
# 3b. Lỗi TẠM THỜI phải được thử lại (bug thật gặp với Gemini: 503)
# =============================================================================

def test_503_overload_is_retried_and_can_succeed(fake_gemini):
    """
    Gemini trả 503 UNAVAILABLE khi quá tải. Đây là lỗi TẠM THỜI:
    thử lại thường thành công, nên tuyệt đối không được bỏ cuộc ngay.
    """
    result, calls = fake_gemini(
        [
            Exception("503 UNAVAILABLE. This model is currently experiencing high demand."),
            VALID_JSON,
        ]
    )

    assert len(calls) == 2
    assert result["next_step"] == "execute"


def test_503_exhausted_reports_unavailable(fake_gemini):
    result, calls = fake_gemini([Exception("503 UNAVAILABLE. high demand.")] * 3)

    assert len(calls) == 3
    assert result["validation"]["code"] == "llm_unavailable"
    assert result["log_result"] == "error: llm_unavailable"
    assert "quá tải" in result["validation"]["message"]


def test_429_rate_limit_is_retried(fake_gemini):
    result, calls = fake_gemini(
        [Exception("429 RESOURCE_EXHAUSTED: rate limit"), VALID_JSON]
    )

    assert len(calls) == 2
    assert result["next_step"] == "execute"


@pytest.mark.parametrize(
    "message",
    [
        "401 UNAUTHENTICATED: API key not valid",
        "403 PERMISSION_DENIED",
        "404 NOT_FOUND: model no longer available",
    ],
)
def test_permanent_errors_are_not_retried(fake_gemini, message):
    """
    Sai key, sai model: thử lại chỉ đốt thêm quota mà vẫn hỏng.
    """
    result, calls = fake_gemini([Exception(message)])

    assert len(calls) == 1, f"{message} không được retry."
    assert result["validation"]["code"] == "llm_api_error"
    assert result["log_result"] == "error: llm_api"


# =============================================================================
# 3d. Tự hạ cấp config khi model không hỗ trợ tính năng
# =============================================================================

BAD_CONFIG = "400 INVALID_ARGUMENT: unsupported field"


def test_400_degrades_config_and_retries(fake_gemini):
    """
    Model không hỗ trợ thinking_config -> 400. Hệ thống phải hạ cấp config
    và thử lại, thay vì chết hẳn.
    """
    result, calls = fake_gemini([Exception(BAD_CONFIG), VALID_JSON])

    assert len(calls) == 2
    assert result["next_step"] == "execute"

    # Lần 1 có thinking_config, lần 2 đã bỏ đi.
    assert hasattr(calls[0]["config"], "thinking_config")
    assert not hasattr(calls[1]["config"], "thinking_config")


def test_400_degrades_twice_dropping_structured_output(fake_gemini):
    """Nếu response_schema cũng bị từ chối -> hạ tiếp, bỏ structured output."""
    result, calls = fake_gemini(
        [Exception(BAD_CONFIG), Exception(BAD_CONFIG), VALID_JSON]
    )

    assert len(calls) == 3
    assert result["next_step"] == "execute"

    assert calls[0].__getitem__("config").response_mime_type == "application/json"
    assert not hasattr(calls[2]["config"], "response_schema")


def test_400_gives_up_after_exhausting_config_levels(fake_gemini):
    result, calls = fake_gemini([Exception(BAD_CONFIG)] * 3)

    assert result["ok"] is False
    assert result["validation"]["code"] == "llm_api_error"


# =============================================================================
# 3c. Phân loại lỗi theo status code
# =============================================================================

@pytest.mark.parametrize(
    "message, expected",
    [
        ("503 UNAVAILABLE", 503),
        ("404 NOT_FOUND: model gone", 404),
        ("429 RESOURCE_EXHAUSTED", 429),
        ("no status code here", None),
    ],
)
def test_extract_status_code(message, expected):
    assert extract_status_code(Exception(message)) == expected


@pytest.mark.parametrize(
    "message, expected_code",
    [
        ("503 UNAVAILABLE", "llm_unavailable"),
        ("500 INTERNAL", "llm_unavailable"),
        ("429 RESOURCE_EXHAUSTED", "llm_rate_limited"),
        ("404 NOT_FOUND", "llm_api_error"),
        ("401 UNAUTHENTICATED", "llm_api_error"),
    ],
)
def test_classify_api_error(message, expected_code):
    assert classify_api_error(Exception(message)).code == expected_code


# =============================================================================
# 4. Thông điệp cho người dùng
# =============================================================================

@pytest.mark.parametrize(
    "outcomes",
    [
        ["rác 1", "rác 2", "rác 3"],
        [FakeTimeoutError("timeout")] * 3,
        [Exception("503 UNAVAILABLE")] * 3,
        [Exception("401 API key not valid")],
    ],
)
def test_failure_never_leaks_raw_exception_to_user(fake_gemini, outcomes):
    result, _ = fake_gemini(outcomes)

    message = result["validation"]["message"]

    assert result["next_step"] == "stop"
    assert "Exception" not in message
    assert "Traceback" not in message
    assert message.strip(), "Người dùng phải nhận được một thông điệp tiếng Việt rõ ràng."


# =============================================================================
# 5. Nhận diện timeout không phụ thuộc thư viện
# =============================================================================

def test_is_timeout_error_detects_by_class_name():
    assert is_timeout_error(FakeTimeoutError("boom")) is True


def test_is_timeout_error_detects_by_message():
    assert is_timeout_error(Exception("request timeout after 15s")) is True


def test_is_timeout_error_rejects_other_errors():
    assert is_timeout_error(Exception("429 quota exceeded")) is False