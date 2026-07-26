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

"""
stt_module.py
--------------
Module nhận diện giọng nói (Speech-To-Text) cho YoloHome, dùng PhoWhisper
(vinai/PhoWhisper) để chuyển audio tiếng Việt thành văn bản.

Implement đúng Contract A (Integration Contracts):

    class STTStrategy(ABC):
        @abstractmethod
        def transcribe(self, audio_data: bytes) -> str: ...

Điểm quan trọng: MỌI audio đầu vào (bất kể định dạng gốc: mp3, m4a, ogg, wav
có sample rate/số kênh khác nhau...) đều được CHUẨN HOÁ về wav mono 16kHz
PCM16 thông qua ffmpeg trước khi đưa vào PhoWhisper. Điều này tránh các lỗi
decode ngầm (đặc biệt với .m4a) và đảm bảo model luôn nhận input đúng định
dạng nó được train.

Cài đặt phụ thuộc (xem thêm requirements.txt):
    pip install torch transformers soundfile numpy
    # ffmpeg phải có sẵn trên hệ thống (Colab đã cài sẵn):
    #   Ubuntu/Debian: sudo apt-get install -y ffmpeg
    # tuỳ chọn, chỉ cần nếu bạn muốn tự thu âm từ mic để test:
    pip install sounddevice
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
from typing import Optional

import numpy as np

logger = logging.getLogger("yolohome.stt")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# STTStrategy: dùng ABC thật của system_core nếu có, fallback nếu chạy độc lập
# (vd. khi dev/test module này trước khi system_core sẵn sàng, hoặc chạy
# trên Colab/unit test ngoài context của repo chính).
# ---------------------------------------------------------------------------
try:
    from system_core.strategies import STTStrategy
except ImportError:  # pragma: no cover - chỉ để dev/test độc lập
    from abc import ABC, abstractmethod

    class STTStrategy(ABC):
        @abstractmethod
        def transcribe(self, audio_data: bytes) -> str: ...


# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------

DEFAULT_MODEL_NAME = os.environ.get("PHOWHISPER_MODEL", "vinai/PhoWhisper-base")
TARGET_SAMPLE_RATE = 16_000  # PhoWhisper (Whisper backbone) yêu cầu 16kHz mono


# ---------------------------------------------------------------------------
# Chuẩn hoá audio bằng ffmpeg (mọi định dạng -> wav mono 16kHz PCM16)
# ---------------------------------------------------------------------------

def normalize_audio_bytes(audio_data: bytes, target_sr: int = TARGET_SAMPLE_RATE) -> bytes:
    """
    Dùng ffmpeg (qua subprocess, pipe stdin/stdout, KHÔNG ghi file tạm ra đĩa)
    để chuẩn hoá bất kỳ audio bytes nào (mp3, m4a, ogg, wav sai sample rate...)
    về wav mono, PCM16, đúng target_sr.

    Args:
        audio_data: bytes audio gốc, định dạng bất kỳ ffmpeg đọc được.
        target_sr: sample rate mong muốn (mặc định 16000, khớp PhoWhisper).

    Returns:
        bytes: nội dung file .wav đã chuẩn hoá.

    Raises:
        RuntimeError: nếu ffmpeg không tồn tại trên hệ thống, hoặc decode lỗi
            (audio hỏng / định dạng không hỗ trợ).
    """
    cmd = [
        "ffmpeg",
        "-y",                 # ghi đè, không hỏi xác nhận
        "-i", "pipe:0",       # đọc input từ stdin
        "-ar", str(target_sr),
        "-ac", "1",           # mono
        "-f", "wav",
        "pipe:1",             # ghi output ra stdout
    ]
    try:
        result = subprocess.run(
            cmd,
            input=audio_data,
            capture_output=True,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Không tìm thấy ffmpeg trên hệ thống. Cài đặt: "
            "'apt-get install ffmpeg' (Colab đã có sẵn ffmpeg mặc định)."
        ) from exc

    if result.returncode != 0:
        stderr_text = result.stderr.decode(errors="ignore")
        raise RuntimeError(f"ffmpeg decode audio thất bại: {stderr_text[-500:]}")

    return result.stdout


def normalize_audio_file(input_path: str, output_path: Optional[str] = None,
                          target_sr: int = TARGET_SAMPLE_RATE) -> str:
    """
    Chuẩn hoá 1 file audio trên đĩa về .wav mono 16kHz, ghi ra output_path.
    Hữu ích khi bạn muốn giữ lại bản .wav đã convert để tái sử dụng, thay vì
    convert lại mỗi lần transcribe (vd. khi build tập dữ liệu đánh giá).

    Args:
        input_path: đường dẫn file audio gốc (mp3/m4a/wav/...).
        output_path: đường dẫn file .wav xuất ra. Nếu None, tự đặt tên
            <input_path>.norm.wav.
        target_sr: sample rate mong muốn.

    Returns:
        str: đường dẫn tới file .wav đã chuẩn hoá.
    """
    if output_path is None:
        base, _ext = os.path.splitext(input_path)
        output_path = f"{base}.norm.wav"

    with open(input_path, "rb") as f:
        raw = f.read()

    wav_bytes = normalize_audio_bytes(raw, target_sr=target_sr)

    with open(output_path, "wb") as f:
        f.write(wav_bytes)

    logger.info(f"Đã chuẩn hoá: {input_path} -> {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# PhoWhisperSTT: cài đặt STTStrategy
# ---------------------------------------------------------------------------

class PhoWhisperSTT(STTStrategy):
    """
    Cài đặt STTStrategy bằng PhoWhisper.

    Model được load lazy (lần transcribe() đầu tiên) và cache trong instance,
    orchestrator nên khởi tạo PhoWhisperSTT() một lần lúc boot và tái sử dụng
    cho toàn bộ vòng đời chương trình.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME, device: Optional[str] = None):
        self.model_name = model_name
        self._device = device
        self._asr_pipeline = None  # load lazy

    def transcribe(self, audio_data: bytes) -> str:
        """
        Args:
            audio_data: bytes audio thô, ĐỊNH DẠNG BẤT KỲ (mp3/m4a/ogg/wav/...).
                Hàm tự chuẩn hoá về wav mono 16kHz bằng ffmpeg trước khi đưa
                vào model. Có thể rỗng.

        Returns:
            str: transcript tiếng Việt CÓ dấu, giữ nguyên hoa/thường và dấu
            câu model sinh ra (KHÔNG lowercase, KHÔNG strip dấu câu ở đây —
            đúng yêu cầu contract). Trả về "" nếu audio rỗng / lỗi decode /
            không nhận diện được gì — KHÔNG raise ra ngoài.
        """
        if not audio_data:
            logger.warning("audio_data rỗng, trả về transcript rỗng.")
            return ""

        try:
            wav_bytes = normalize_audio_bytes(audio_data)
        except Exception:
            logger.exception("Chuẩn hoá audio thất bại, trả về transcript rỗng.")
            return ""

        try:
            waveform = self._wav_bytes_to_array(wav_bytes)
        except Exception:
            logger.exception("Không đọc được wav sau chuẩn hoá, trả về transcript rỗng.")
            return ""

        if waveform.size == 0:
            logger.warning("Waveform rỗng (có thể là im lặng), trả về transcript rỗng.")
            return ""

        waveform = self._normalize_amplitude(waveform)
        asr = self._get_pipeline()

        try:
            result = asr(
                waveform,
                generate_kwargs={"language": "vi", "task": "transcribe"},
            )
        except TypeError:
            result = asr(waveform)
        except Exception:
            logger.exception("Lỗi khi chạy PhoWhisper, trả về transcript rỗng.")
            return ""

        text = (result.get("text") or "").strip()
        logger.info(f"Transcript: {text!r}")
        return text

    # -- Tiện ích cho việc test/đánh giá (không thuộc contract bắt buộc) --

    def transcribe_file(self, path: str) -> str:
        """Tiện ích: đọc file audio từ đĩa (định dạng bất kỳ) rồi transcribe."""
        with open(path, "rb") as f:
            return self.transcribe(f.read())

    # -- Helpers nội bộ ----------------------------------------------------

    def _get_pipeline(self):
        if self._asr_pipeline is not None:
            return self._asr_pipeline

        import torch
        from transformers import pipeline

        device = self._device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(f"Đang tải model PhoWhisper: {self.model_name} (device={device}) ...")
        self._asr_pipeline = pipeline(
            task="automatic-speech-recognition",
            model=self.model_name,
            device=0 if device == "cuda" else -1,
            chunk_length_s=30,
            stride_length_s=5,
        )
        logger.info("Tải model thành công.")
        return self._asr_pipeline

    @staticmethod
    def _wav_bytes_to_array(wav_bytes: bytes) -> np.ndarray:
        """Đọc wav bytes (đã chuẩn hoá mono 16kHz) -> numpy float32."""
        import soundfile as sf

        waveform, _sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if waveform.ndim > 1:  # phòng hờ, dù ffmpeg đã ép mono
            waveform = waveform.mean(axis=1)
        return waveform

    @staticmethod
    def _normalize_amplitude(waveform: np.ndarray) -> np.ndarray:
        max_val = np.abs(waveform).max()
        if max_val > 0:
            waveform = waveform / max_val * 0.95
        return waveform


# ---------------------------------------------------------------------------
# (Tuỳ chọn) Thu âm trực tiếp từ microphone -> trả về bytes wav
# ---------------------------------------------------------------------------

def record_wav_bytes(duration: float = 4.0, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """
    Ghi âm `duration` giây từ mic mặc định, trả về bytes WAV (PCM16).
    Yêu cầu: pip install sounddevice soundfile
    LƯU Ý: không chạy được trên Colab (không có mic vật lý gắn vào kernel).
    """
    import sounddevice as sd
    import soundfile as sf

    logger.info(f"Đang ghi âm trong {duration}s ...")
    recording = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    logger.info("Ghi âm xong.")

    buf = io.BytesIO()
    sf.write(buf, recording, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Chạy thử qua terminal:
#   python stt_module.py <file_audio>      -> test với file có sẵn (mọi định dạng)
#   python stt_module.py                    -> ghi âm 4s từ mic rồi test (KHÔNG chạy trên Colab)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    stt = PhoWhisperSTT()

    if len(sys.argv) > 1:
        path = sys.argv[1]
        print(f"Đang nhận diện file: {path}")
        text = stt.transcribe_file(path)
    else:
        print("Không có file audio -> ghi âm 4 giây từ micro để test.")
        audio_bytes = record_wav_bytes(duration=4.0)
        text = stt.transcribe(audio_bytes)

    print("Kết quả transcript:", text)