"""
test_with_real_voice.py
------------------------- 
Cài đặt (nếu chưa có):
    pip install sounddevice soundfile

Chạy:
    python tools/test_stt.py
    python tools/test_stt.py --duration 5 
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STT_MODULE_DIR = PROJECT_ROOT / "modules" / "speech_recognition"
sys.path.insert(0, str(STT_MODULE_DIR))

from stt_module import PhoWhisperSTT, record_wav_bytes  # noqa: E402


def run_one_round(stt: PhoWhisperSTT, duration: float) -> None:
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
    parser = argparse.ArgumentParser(description="Test PhoWhisperSTT với giọng nói thật qua mic.")
    parser.add_argument("--duration", type=float, default=4.0, help="Số giây ghi âm mỗi lần (mặc định 4s)")
    parser.add_argument("--model", type=str, default=None, help="Tên model PhoWhisper (mặc định lấy từ stt_module.py)")
    args = parser.parse_args()

    kwargs = {"model_name": args.model} if args.model else {}
    stt = PhoWhisperSTT(**kwargs)

    print(f"Đang test với model: {stt.model_name}")
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