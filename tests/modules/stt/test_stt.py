"""
Công cụ kiểm tra thủ công STT module (PhoWhisper).

ĐÂY KHÔNG PHẢI TEST TỰ ĐỘNG. File cần micro thật, cần ffmpeg trên PATH, và
lần chạy đầu sẽ tải model vài trăm MB về máy. Không có assert nào.

Chạy:
    python -m tests.modules.stt.test_stt
    python -m tests.modules.stt.test_stt --duration 5
    python -m tests.modules.stt.test_stt --file data/samples/bat_den.wav
    python -m tests.modules.stt.test_stt --model ./models/phowhisper-finetuned

Cài thêm nếu chưa có:
    pip install sounddevice soundfile

VÌ SAO IMPORT NẶNG NẰM TRONG HÀM
--------------------------------
Tên file bắt đầu bằng "test_" nên pytest sẽ IMPORT nó mỗi lần chạy suite.
stt_module kéo theo numpy và gọi logging.basicConfig() ngay lúc import - tức
là chỉ cần pytest quét qua file này, cấu hình logging của cả test session đã
bị ghi đè. Đưa import vào trong hàm thì pytest import file thành công, thấy
không có hàm test_* nào, thu thập 0 test rồi đi tiếp.

VÌ SAO KHÔNG CÒN sys.path.insert
--------------------------------
Bản cũ chèn thẳng modules/speech_recognition vào sys.path rồi
`from stt_module import ...`. Cách đó BỎ QUA package, nên bên trong
stt_module dòng `from system_core.strategies import STTStrategy` thất bại và
rơi vào khối `except ImportError` - nó tự định nghĩa một ABC TRÙNG TÊN nhưng
KHÁC ĐỐI TƯỢNG. Hậu quả: công cụ này chạy ngon lành, còn
isinstance(stt, STTStrategy) trong hệ thống thật lại trả False. Lỗi chỉ lộ
ra lúc tích hợp.

Giờ import theo đúng đường package, và hàm _check_strategy_identity() bên
dưới kiểm tra lại chuyện đó một cách tường minh.
"""

from __future__ import annotations

import argparse
import shutil
import time

# pytest: file này không chứa test tự động nào.
__test__ = False


def _check_ffmpeg() -> bool:
    """
    ffmpeg là phụ thuộc hệ thống, KHÔNG cài được qua pip.

    Thiếu nó thì PhoWhisperSTT.transcribe() trả chuỗi rỗng IM LẶNG - đúng
    hợp đồng (không raise), nhưng lúc demo sẽ trông như "model không nghe
    được gì". Kiểm tra ngay từ đầu để khỏi đoán mò.
    """
    if shutil.which("ffmpeg"):
        return True

    print("[LỖI] Không tìm thấy ffmpeg trên PATH.")
    print("  Windows : winget install Gyan.FFmpeg")
    print("  Linux   : sudo apt-get install ffmpeg")
    print("  macOS   : brew install ffmpeg")
    print("Thiếu ffmpeg thì mọi transcript sẽ rỗng mà không báo lỗi.")
    return False


def _check_strategy_identity(stt) -> None:
    """
    Xác nhận stt là STTStrategy THẬT của hệ thống.

    Nếu False, nghĩa là stt_module đã rơi vào ABC dự phòng trong khối
    `except ImportError` - đường import đang sai, và CommandService sẽ không
    nhận ra engine này.
    """
    try:
        from system_core.strategies import STTStrategy
    except ImportError as exc:
        print(f"[CẢNH BÁO] Không import được system_core.strategies: {exc}")
        print("Hãy chạy từ thư mục gốc repo bằng: python -m tests.modules.stt.test_stt")
        return

    if not isinstance(stt, STTStrategy):
        print("[CẢNH BÁO NGHIÊM TRỌNG] Engine KHÔNG phải STTStrategy của hệ thống.")
        print("stt_module đã rơi vào ABC dự phòng. Công cụ này vẫn chạy được,")
        print("nhưng khi tích hợp vào pipeline thì kiểm tra hợp đồng sẽ trượt.")


def run_one_round(stt, duration: float) -> None:
    from modules.speech_recognition.stt_module import record_wav_bytes

    input(f"\n>> Nhấn Enter rồi nói ngay (sẽ ghi âm {duration}s)...")
    audio_bytes = record_wav_bytes(duration=duration)

    print("Đang nhận diện...")
    start_time = time.time()
    text = stt.transcribe(audio_bytes)
    latency = time.time() - start_time

    print("-" * 60)
    if text:
        print(f"Transcript : {text}")
    else:
        print("Transcript : (RỖNG)")
        print("  Nguyên nhân thường gặp: nói quá nhỏ, sai micro mặc định,")
        print("  hoặc ffmpeg decode lỗi. Xem log WARNING phía trên.")
    print(f"Độ trễ     : {latency:.2f}s cho {duration:.1f}s audio")
    print("-" * 60)


def run_file(stt, path: str) -> None:
    """Transcribe một file audio có sẵn - không cần micro, lặp lại được."""
    print(f"\nĐang đọc {path} ...")
    start_time = time.time()
    text = stt.transcribe_file(path)
    latency = time.time() - start_time

    print("-" * 60)
    print(f"Transcript : {text or '(RỖNG)'}")
    print(f"Độ trễ     : {latency:.2f}s")
    print("-" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kiểm tra PhoWhisperSTT bằng giọng nói thật qua micro."
    )
    parser.add_argument(
        "--duration", type=float, default=4.0,
        help="Số giây ghi âm mỗi lượt (mặc định 4s)",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Tên/đường dẫn model PhoWhisper. Trỏ vào checkpoint đã fine-tune "
             "để so sánh với bản gốc.",
    )
    parser.add_argument(
        "--file", type=str, default=None,
        help="Transcribe một file audio có sẵn thay vì ghi âm. Dùng để đo độ "
             "trễ lặp lại được cho báo cáo, không cần micro.",
    )
    args = parser.parse_args()

    if not _check_ffmpeg():
        return

    from modules.speech_recognition.stt_module import PhoWhisperSTT

    kwargs = {"model_name": args.model} if args.model else {}
    stt = PhoWhisperSTT(**kwargs)

    _check_strategy_identity(stt)

    print(f"\nModel: {stt.model_name}")
    print("Lần chạy đầu sẽ tải model về máy, các lần sau nhanh hơn nhiều.")
    print("Ctrl+C để dừng bất kỳ lúc nào.\n")

    if args.file:
        run_file(stt, args.file)
        return

    round_num = 1
    try:
        while True:
            print(f"\n===== Lượt {round_num} =====")
            run_one_round(stt, args.duration)

            again = input("\nTest thêm lượt nữa? (Enter = có / n = dừng): ")
            if again.strip().lower() == "n":
                break
            round_num += 1
    except KeyboardInterrupt:
        print("\nĐã dừng.")


if __name__ == "__main__":
    main()