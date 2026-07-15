"""
Speech-to-Text module.

OWNER: STT module.

Vai trò trong pipeline: biến audio bytes thành transcript tiếng Việt để đưa
vào LLM. Hợp đồng duy nhất: bytes -> str (tiếng Việt tự nhiên, CÓ dấu).

Xem hợp đồng đầy đủ: docs/Integration-Contracts.md (Contract A).
"""

from __future__ import annotations

from system_core.strategies import STTStrategy


class MockSTTStrategy(STTStrategy):
    """
    STT giả cho test/demo offline. Trả về một transcript đặt sẵn thay vì
    thực sự giải mã audio - cho phép chạy toàn bộ pipeline không cần micro/model.

        stt = MockSTTStrategy("bật đèn phòng khách")
        stt.transcribe(b"")  # -> "bật đèn phòng khách"
    """

    def __init__(self, transcript: str = "bật đèn phòng khách") -> None:
        self.transcript = transcript

    def transcribe(self, audio_data: bytes = b"") -> str:
        return self.transcript


# TODO(STT owner): implement engine thật (đã có ctranslate2 + soundfile trong
# requirements.txt - gợi ý faster-whisper / PhoWhisper cho tiếng Việt).
#
# class WhisperSTTStrategy(STTStrategy):
#     def __init__(self, model_size: str = "base") -> None:
#         # load model (ctranslate2)
#         ...
#
#     def transcribe(self, audio_data: bytes) -> str:
#         # 1. decode audio_data -> waveform (soundfile)
#         # 2. chạy model -> text
#         # 3. return text tiếng Việt CÓ DẤU, KHÔNG lowercase, KHÔNG bỏ dấu câu
#         #    (llm_module tự lowercase ở nhánh mock; Gemini cần chuỗi thô)
#         raise NotImplementedError


__all__ = ["MockSTTStrategy"]
