"""
test_stt.py
-------------------------
Script test THỦ CÔNG (không phải pytest) - ghi âm giọng nói thật từ mic
và chạy qua PhoWhisperSTT HOẶC FasterWhisperSTT để so sánh transcript thực tế.

KHÔNG chạy trên Google Colab (không có mic vật lý gắn vào kernel) - chỉ
chạy trên máy có mic thật (laptop/PC của bạn).

Vị trí: tools/test_stt.py (cùng cấp với modules/)

Cài đặt (nếu chưa có):
    pip install sounddevice soundfile
    # nếu test faster-whisper: pip install faster-whisper ctranslate2
    # và đã convert model (xem hướng dẫn đầu stt_faster_whisper.py)

Chạy:
    python tools/test_stt.py
    python tools/test_stt.py --duration 5
    python tools/test_stt.py --engine faster-whisper
    python tools/test_stt.py --engine faster-whisper --ct2-path "D:\duong\dan\khac\model-ct2"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# tools/ nằm CÙNG CẤP với modules/, không phải bên trong modules/speech_recognition/
# -> phải trỏ rõ đường dẫn tới module cần test.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STT_MODULE_DIR = PROJECT_ROOT / "modules" / "speech_recognition"
sys.path.insert(0, str(STT_MODULE_DIR))

from stt_module import STTStrategy, PhoWhisperSTT, record_wav_bytes  # noqa: E402
from stt_faster_whisper import FasterWhisperSTT, DEFAULT_CT2_MODEL_PATH  # noqa: E402


def build_stt(args: argparse.Namespace) -> STTStrategy:
    """Khởi tạo đúng STT strategy theo --engine, in ra thông tin model đang dùng."""
    if args.engine == "faster-whisper":
        stt = FasterWhisperSTT(
            model_path=args.ct2_path,
            device="cpu",
            compute_type="int8",
        )
        print(f"Đang test với engine: faster-whisper (CTranslate2)")
        print(f"Model path: {args.ct2_path}")
        return stt

    kwargs = {"model_name": args.model} if args.model else {}
    stt = PhoWhisperSTT(**kwargs)
    print(f"Đang test với engine: PhoWhisperSTT (transformers)")
    print(f"Model: {stt.model_name}")
    return stt


def run_one_round(stt: STTStrategy, duration: float) -> None:
    input(f"\n>> Nhấn Enter rồi nói ngay (sẽ ghi âm {duration}s)...")
    audio_bytes = record_wav_bytes(duration=duration)

    print("Đang nhận diện...")
    text = stt.transcribe(audio_bytes)

    print("-" * 50)
    if text:
        print(f"Transcript: {text}")
    else:
        print("Transcript: (rỗng - không nhận diện được gì, thử nói to/rõ hơn)")
    print("-" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="Test STT (PhoWhisperSTT hoặc FasterWhisperSTT) với giọng nói thật qua mic."
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="phowhisper",
        choices=["phowhisper", "faster-whisper"],
        help="Chọn engine STT để test (mặc định: phowhisper)",
    )
    parser.add_argument(
        "--duration", type=float, default=4.0,
        help="Số giây ghi âm mỗi lần (mặc định 4s)",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Tên model PhoWhisper (chỉ dùng khi --engine phowhisper, mặc định lấy từ stt_module.py)",
    )
    parser.add_argument(
        "--ct2-path", type=str, default=DEFAULT_CT2_MODEL_PATH,
        help="Đường dẫn model đã convert CTranslate2 (chỉ dùng khi --engine faster-whisper). "
             f"Mặc định: {DEFAULT_CT2_MODEL_PATH}",
    )
    args = parser.parse_args()

    stt = build_stt(args)

    print("Lần đầu chạy sẽ mất thời gian tải model, các lần sau sẽ nhanh hơn.")
    print("Gõ Ctrl+C bất kỳ lúc nào để dừng.\n")

    round_num = 1
    try:
        while True:
            print(f"\n===== Lượt {round_num} =====")
            run_one_round(stt, args.duration)

            again = input("\nTest thêm lượt nữa? (Enter = có / n = dừng): ").strip().lower()
            if again == "n":
                break
            round_num += 1
    except KeyboardInterrupt:
        print("\nĐã dừng test.")


if __name__ == "__main__":
    main()