from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class STTStrategy(ABC):
    """
    Strategy interface for Speech-to-Text engines.

    The main pipeline should depend on this interface instead of depending
    directly on a specific STT model or library.

    Example concrete implementations:
    - PhoWhisperSTTStrategy
    - GoogleSTTStrategy
    - MockSTTStrategy
    """

    @abstractmethod
    def transcribe(self, audio_data: bytes) -> str:
        """
        Convert audio bytes into a text transcript.

        Args:
            audio_data: Raw audio data in bytes.

        Returns:
            Transcribed text.
        """
        raise NotImplementedError


class LLMStrategy(ABC):
    """
    Strategy interface for LLM-based command understanding.

    CommandService should depend on this interface instead of calling
    Gemini, mock parser, or any concrete LLM module directly.

    Example concrete implementations:
    - GeminiLLMStrategy
    - MockLLMStrategy
    - LocalLLMStrategy
    """

    @abstractmethod
    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        pending_command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Parse a text transcript into a validated command object.

        Args:
            transcript: User command text, usually from STT or text input.
            sensor_data: Optional current sensor values for context-aware commands.
            pending_command: Command đang chờ làm rõ từ lượt trước, nếu người
                dùng đang trả lời một câu hỏi làm rõ (multi-turn slot filling).

        Returns:
            A standard LLM result dictionary:
            {
                "ok": bool,
                "transcript": str,
                "command": dict | None,
                "validation": dict,
                "next_step": str,
                "latency_ms": int,
                "log_result": str | None,
                "error": str | None
            }
        """
        raise NotImplementedError