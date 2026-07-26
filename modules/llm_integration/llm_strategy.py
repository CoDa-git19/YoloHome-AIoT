from __future__ import annotations

from typing import Any

from modules.llm_integration.llm_module import parse_and_validate
from system_core.strategies import LLMStrategy


class GeminiLLMStrategy(LLMStrategy):
    """
    Concrete LLM Strategy for the project LLM pipeline.

    This strategy delegates parsing and validation to llm_module.parse_and_validate().

    Depending on use_mock:
    - use_mock=True  -> use mock parser for tests/demo
    - use_mock=False -> allow llm_module to call Gemini if GEMINI_API_KEY is available
    """

    def __init__(
        self,
        use_mock: bool | None = None,
        model: str | None = None,
    ) -> None:
        self.use_mock = use_mock
        # model=None -> dùng GEMINI_MODEL trong .env.
        # Truyền model cụ thể để benchmark nhiều model song song.
        self.model = model

    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        pending_command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return parse_and_validate(
            transcript=transcript,
            sensor_data=sensor_data,
            pending_command=pending_command,
            model=self.model,
            use_mock=self.use_mock,
        )


class MockLLMStrategy(LLMStrategy):
    """
    Explicit mock LLM Strategy for unit tests and offline demo.

    This class always forces use_mock=True, so it never calls Gemini API.
    """

    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        pending_command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return parse_and_validate(
            transcript=transcript,
            sensor_data=sensor_data,
            pending_command=pending_command,
            use_mock=True,
        )


__all__ = [
    "GeminiLLMStrategy",
    "MockLLMStrategy",
]