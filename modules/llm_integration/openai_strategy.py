"""
OpenAI LLM Strategy.

Mục đích kép:

1. Cho phép benchmark Gemini và OpenAI trên cùng một bộ dữ liệu.
2. Chứng minh Strategy Pattern có giá trị THẬT, không phải pattern trang trí:
   thêm hẳn một nhà cung cấp LLM mới mà KHÔNG sửa một dòng nào trong
   CommandService, validator, command factory hay hardware gateway.

Toàn bộ phần dùng chung được tái sử dụng nguyên vẹn:
    build_prompt()          - cùng một prompt, sinh từ config
    build_response_schema() - cùng một JSON schema, sinh từ device_registry
    extract_json_object()   - cùng cách trích JSON
    normalize_command()     - cùng cách chuẩn hoá
    enforce_policy()        - cùng chính sách an ninh phía server
    validate_command()      - cùng bộ validator
    determine_next_step()   - cùng bộ định tuyến

Chỉ đúng một thứ khác: lời gọi HTTP tới nhà cung cấp.

Cần OPENAI_API_KEY trong .env. Không có key thì strategy này raise ngay
khi khởi tạo, không âm thầm rơi về mock.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from config.settings import (
    LLM_MAX_RETRIES,
    LLM_RETRY_BACKOFF_SECONDS,
    LLM_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from modules.llm_integration.llm_module import (
    LLMAPIError,
    LLMError,
    LLMParseError,
    RETRYABLE_ERRORS,
    build_prompt,
    build_response_schema,
    classify_api_error,
    determine_next_step,
    extract_json_object,
    load_device_registry,
    load_schema,
)
from modules.llm_integration.validator import (
    enforce_policy,
    normalize_command,
    validate_command,
    validation_code_to_log_result,
)
from system_core.strategies import LLMStrategy

logger = logging.getLogger(__name__)


def to_openai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Đổi response schema kiểu Gemini (OBJECT/STRING) sang kiểu JSON Schema
    của OpenAI (object/string).

    Cùng một nguồn sự thật là device_registry.json, chỉ khác cú pháp.
    """
    type_map = {
        "OBJECT": "object",
        "STRING": "string",
        "BOOLEAN": "boolean",
        "NUMBER": "number",
        "ARRAY": "array",
    }

    converted: dict[str, Any] = {}

    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            converted["type"] = type_map.get(value, value.lower())
        elif key == "nullable":
            continue  # OpenAI dùng type: [x, "null"] thay vì cờ nullable
        elif key == "properties" and isinstance(value, dict):
            converted["properties"] = {
                name: to_openai_schema(prop) for name, prop in value.items()
            }
        else:
            converted[key] = value

    # Chuyển nullable -> union type
    if schema.get("nullable") and "type" in converted:
        converted["type"] = [converted["type"], "null"]

    if converted.get("type") == "object":
        converted.setdefault("additionalProperties", False)

    return converted


class OpenAILLMStrategy(LLMStrategy):
    """Concrete LLM Strategy dùng OpenAI Chat Completions."""

    def __init__(self, model: str | None = None) -> None:
        if not OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY chưa được set trong .env. "
                "OpenAILLMStrategy không tự rơi về mock."
            )

        self.model = model or OPENAI_MODEL

    # =========================================================================
    # Gọi API
    # =========================================================================

    def _call(self, prompt: str, device_registry: dict[str, Any]) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY, timeout=LLM_TIMEOUT_SECONDS)

        schema = to_openai_schema(build_response_schema(device_registry))

        try:
            response = client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "smart_home_command",
                        "strict": True,
                        "schema": schema,
                    },
                },
            )
        except Exception as exc:
            raise classify_api_error(exc) from exc

        return response.choices[0].message.content or ""

    # =========================================================================
    # LLMStrategy
    # =========================================================================

    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        pending_command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = time.time()

        device_registry = load_device_registry()
        command_schema = load_schema()

        try:
            command = self._parse_with_retries(
                transcript=transcript,
                sensor_data=sensor_data,
                pending_command=pending_command,
                device_registry=device_registry,
                command_schema=command_schema,
            )

            command = normalize_command(command, command_schema=command_schema)

            # Cùng một chính sách an ninh phía server như Gemini.
            command, policy_overrides = enforce_policy(
                command,
                command_schema=command_schema,
            )

            validation = validate_command(
                command=command,
                device_registry=device_registry,
                command_schema=command_schema,
            )

            return {
                "ok": bool(validation["passed"]),
                "engine": "openai",
                "model": self.model,
                "transcript": transcript,
                "command": command,
                "validation": validation,
                "policy_overrides": policy_overrides,
                "next_step": determine_next_step(command, validation),
                "latency_ms": int((time.time() - start) * 1000),
                "log_result": None
                if validation["passed"]
                else validation_code_to_log_result(validation["code"]),
                "error": None if validation["passed"] else validation["message"],
            }

        except LLMError as exc:
            logger.error("OpenAI failure (%s): %s", exc.code, exc)

            return {
                "ok": False,
                "engine": "openai",
                "model": self.model,
                "transcript": transcript,
                "command": None,
                "validation": {
                    "passed": False,
                    "code": exc.code,
                    "message": exc.user_message,
                },
                "policy_overrides": [],
                "next_step": "stop",
                "latency_ms": int((time.time() - start) * 1000),
                "log_result": exc.log_result,
                "error": str(exc),
            }

    def _parse_with_retries(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None,
        pending_command: dict[str, Any] | None,
        device_registry: dict[str, Any],
        command_schema: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = build_prompt(
            transcript=transcript,
            sensor_data=sensor_data,
            device_registry=device_registry,
            pending_command=pending_command,
            command_schema=command_schema,
        )

        total_attempts = max(1, LLM_MAX_RETRIES + 1)
        last_error: LLMError | None = None

        for attempt in range(1, total_attempts + 1):
            if attempt > 1:
                time.sleep(LLM_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 2)))

            try:
                raw_text = self._call(prompt, device_registry)
            except LLMAPIError:
                raise
            except RETRYABLE_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "OpenAI call failed (%s, attempt %d/%d): %s",
                    exc.code,
                    attempt,
                    total_attempts,
                    exc,
                )
                continue

            try:
                return extract_json_object(raw_text)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = LLMParseError(f"OpenAI did not return valid JSON: {exc}")
                continue

        raise last_error or LLMParseError("OpenAI failed to return a valid command.")


__all__ = ["OpenAILLMStrategy", "to_openai_schema"]