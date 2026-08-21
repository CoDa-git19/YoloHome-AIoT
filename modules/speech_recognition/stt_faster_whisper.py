"""
Speech-to-Text module — faster-whisper (CTranslate2) implementation.

OWNER: STT module.

Cài đặt STTStrategy thay thế cho PhoWhisperSTT (stt_module.py), dùng
faster-whisper (CTranslate2 backend) thay vì transformers/PyTorch. Nhanh
hơn đáng kể trên CPU (khoảng 4-8 lần trong thực tế), phù hợp khi triển khai
trên máy không có GPU. Cùng hợp đồng: bytes -> str (tiếng Việt tự nhiên, CÓ dấu).

Xem hợp đồng đầy đủ: docs/Integration-Contracts.md (Contract A).

--------------------------------------------------------------------------
BƯỚC BẮT BUỘC TRƯỚC KHI DÙNG - convert model sang định dạng CTranslate2:

    pip install faster-whisper ctranslate2

    ct2-transformers-converter \
        --model ./modules/speech_recognition/finetune_final \
        --output_dir ./modules/speech_recognition/finetune_final_ct2 \
        --quantization int8

Nếu sau này fine-tune lại PhoWhisper (xem finetune_phowhisper.py), chạy lại
lệnh convert trên với checkpoint mới để có bản CTranslate2 tương ứng.
--------------------------------------------------------------------------
"""

from __future__ import annotations
import io
import logging
import numpy as np
from pathlib import Path

from .stt_module import STTStrategy, normalize_audio_bytes

logger = logging.getLogger("yolohome.stt.faster_whisper")
logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------
_MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_CT2_MODEL_PATH = str(_MODULE_DIR / "finetune_final_ct2")

class FasterWhisperSTT(STTStrategy):
    """
    Cài đặt STTStrategy bằng faster-whisper (CTranslate2).

    Model được load lazy (lần transcribe() đầu tiên) và cache trong instance,
    orchestrator nên khởi tạo FasterWhisperSTT() một lần lúc boot và tái sử
    dụng cho toàn bộ vòng đời chương trình - giống hệt cách dùng PhoWhisperSTT.

        stt = FasterWhisperSTT(model_path="./finetune_final_ct2")
        stt.transcribe(audio_bytes)  # -> "bật đèn phòng khách"

    Args:
        model_path: đường dẫn tới thư mục model ĐÃ CONVERT sang CTranslate2
            (xem hướng dẫn convert ở đầu file). KHÔNG phải tên repo HF gốc.
        device: "cpu" hoặc "cuda". Mặc định "cpu" vì mục đích chính của
            class này là tăng tốc inference trên máy không có GPU.
        compute_type: kiểu lượng tử hoá - "int8" (nhẹ nhất, khuyên dùng cho
            CPU), "int8_float16" (cần GPU), "float16" (GPU), "float32"
            (chính xác nhất, chậm nhất).
    """

    def __init__(
        self,
        model_path: str = DEFAULT_CT2_MODEL_PATH,
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self._model = None  # load lazy

    def transcribe(self, audio_data: bytes) -> str:
        """
        Args:
            audio_data: bytes audio thô, ĐỊNH DẠNG BẤT KỲ (mp3/m4a/ogg/wav/...).
                Hàm tự chuẩn hoá về wav mono 16kHz bằng ffmpeg trước khi đưa
                vào model (tái sử dụng đúng normalize_audio_bytes() của
                stt_module.py, đảm bảo audio train/inference xử lý nhất quán).
                Có thể rỗng.

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

            model = self._get_model()
 
            segments, _info = model.transcribe(
                waveform,
                language="vi",
                task="transcribe",
                beam_size=5,
            )

            text = " ".join(segment.text for segment in segments).strip()
            logger.info(f"Transcript: {text!r}")
            return text

        except Exception:
            logger.exception("Lỗi khi chạy faster-whisper, trả về transcript rỗng.")
            return ""

    def transcribe_file(self, path: str) -> str:
        """Tiện ích: đọc file audio từ đĩa (định dạng bất kỳ) rồi transcribe."""
        with open(path, "rb") as f:
            return self.transcribe(f.read())

    def _get_model(self):
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel

        logger.info(
            f"Đang tải model faster-whisper: {self.model_path} "
            f"(device={self.device}, compute_type={self.compute_type}) ..."
        )
        self._model = WhisperModel(
            self.model_path,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("Tải model thành công.")
        return self._model

    @staticmethod
    def _wav_bytes_to_array(wav_bytes: bytes) -> np.ndarray:
        """Đọc wav bytes (đã chuẩn hoá mono 16kHz) -> numpy float32."""
        import soundfile as sf

        waveform, _sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if waveform.ndim > 1:
            waveform = waveform.mean(axis=1)
        return waveform


__all__ = [
    "FasterWhisperSTT",
    "DEFAULT_CT2_MODEL_PATH",
]