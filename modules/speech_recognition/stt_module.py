"""
Speech-to-Text module.

OWNER: STT module.

Vai trò trong pipeline: biến audio bytes thành transcript tiếng Việt để đưa
vào LLM. Hợp đồng duy nhất: bytes -> str (tiếng Việt tự nhiên, CÓ dấu).

Xem hợp đồng đầy đủ: docs/Integration-Contracts.md (Contract A).
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

try:
    from system_core.strategies import STTStrategy
except ImportError:  
    from abc import ABC, abstractmethod

    class STTStrategy(ABC):
        @abstractmethod
        def transcribe(self, audio_data: bytes) -> str: ...


# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------
DEFAULT_MODEL_NAME = os.environ.get("PHOWHISPER_MODEL", "vinai/PhoWhisper-base")
TARGET_SAMPLE_RATE = 16_000  # PhoWhisper (Whisper backbone) yêu cầu 16kHz mono


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
            "'apt-get install ffmpeg'"
        ) from exc

    if result.returncode != 0:
        stderr_text = result.stderr.decode(errors="ignore")
        raise RuntimeError(f"ffmpeg decode audio thất bại: {stderr_text[-500:]}")

    return result.stdout

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
            waveform = self._wav_bytes_to_array(wav_bytes) 

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
            
            text = (result.get("text") or "").strip()
            logger.info(f"Transcript: {text!r}")
            return text
    
        except Exception:
            logger.exception("Lỗi khi chạy PhoWhisper, trả về transcript rỗng.")
            return ""

    def transcribe_file(self, path: str) -> str:
        """Tiện ích: đọc file audio từ đĩa (định dạng bất kỳ) rồi transcribe."""
        with open(path, "rb") as f:
            return self.transcribe(f.read())

    def _get_pipeline(self):
        if self._asr_pipeline is not None:
            return self._asr_pipeline

        import torch
        from transformers import pipeline

        device = self._device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(f"Đang tải model PhoWhisper: {self.model_name} (device={device}) ...")

        pipeline_kwargs = dict(
            task="automatic-speech-recognition",
            model=self.model_name,
            device=0 if device == "cuda" else -1,
        )

        # THỬ pytorch_model.bin TRƯỚC, .safetensors chỉ là đường lùi.
        #
        # Thứ tự này là CÓ CHỦ ĐÍCH và không thể đảo lại.
        #
        # Đường .safetensors có thể GIẾT TIẾN TRÌNH ở tầng C++: chương trình
        # thoát im lặng ngay sau khi tải model xong - không traceback, không
        # exit code, không log. `except Exception` KHÔNG bắt được vì Python
        # không bao giờ được trao lại quyền điều khiển.
        # Kiểm chứng trên Windows/CPU với PhoWhisper-base, cache sạch:
        #     pipeline(...)                            -> chết im lặng
        #     from_pretrained(use_safetensors=False)   -> OK
        #
        # Ngược lại, thiếu pytorch_model.bin chỉ ném OSError bình thường - bắt
        # được, xử lý được. Nên đặt đường an toàn trước thì mọi máy đều chạy:
        #   - máy có .bin (PhoWhisper gốc)      -> dùng .bin, không rủi ro
        #   - máy chỉ có .safetensors (một số   -> lần thử đầu ném OSError,
        #     checkpoint fine-tune bản mới)        rơi xuống đường thứ hai
        try:
            self._asr_pipeline = pipeline(
                **pipeline_kwargs,
                model_kwargs={"use_safetensors": False},
            )
        except Exception as exc:
            logger.warning(
                "Không nạp được pytorch_model.bin (%s), thử .safetensors.", exc
            )
            self._asr_pipeline = pipeline(**pipeline_kwargs)

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


__all__ = [
    "STTStrategy",
    "MockSTTStrategy",
    "PhoWhisperSTT",
    "normalize_audio_bytes",
    "record_wav_bytes",
    "DEFAULT_MODEL_NAME",
    "TARGET_SAMPLE_RATE",
]
