"""
evaluate_models.py
-------------------
Đánh giá chất lượng STT ĐÃ ĐÓNG GÓI qua STTStrategy (PhoWhisperSTT hoặc
FasterWhisperSTT), dùng transcribe(bytes) -> str - phản ánh đúng hành vi
khi module chạy thật trong production (kể cả bước chuẩn hoá ffmpeg bên
trong transcribe()).

Khác với evaluate_finetune.py: file đó đánh giá TRỰC TIẾP model HuggingFace
thô (model.generate()) trong lúc đang thao tác checkpoint để fine-tune;
file này đánh giá STTStrategy đã đóng gói, dùng để verify module production
trước khi bàn giao.

Chạy lệnh:
    python evaluate_model.py --engine phowhisper
    python modules/speech_recognition/evaluate_model.py --engine faster-whisper --ct2-path ./modules/speech_recognition/finetune_final_ct2

Yêu cầu:
    pip install jiwer pandas
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import List

import pandas as pd

from stt_module import STTStrategy, PhoWhisperSTT, DEFAULT_MODEL_NAME
from stt_faster_whisper import FasterWhisperSTT, DEFAULT_CT2_MODEL_PATH
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = _MODULE_DIR / "samples"

# ---------------------------------------------------------------------------
# 1. Định nghĩa tập test: (đường dẫn file audio, transcript ĐÚNG - ground truth) 
# ---------------------------------------------------------------------------

@dataclass
class TestSample:
    audio_path: str
    ground_truth: str
    category: str = "command" 


TEST_SET: List[TestSample] = [
    TestSample(str(SAMPLES_DIR / "bat-den-phong-khach.wav"), "bật đèn phòng khách"),
    TestSample(str(SAMPLES_DIR / "tat-den-nha-ve-sinh.wav"), "tắt đèn nhà vệ sinh"),
    TestSample(str(SAMPLES_DIR / "tat-quat-di.wav"), "tắt quạt đi")
    # ... thêm các câu lệnh thực tế khác của bạn ở đây
    # TestSample("samples/silence_1.wav", "", category="silence"),
]



# ---------------------------------------------------------------------------
# 2. Khởi tạo đúng STTStrategy theo engine được chọn
# ---------------------------------------------------------------------------

def build_stt(engine: str, model_name: str | None, ct2_path: str) -> STTStrategy:
    """Khởi tạo PhoWhisperSTT hoặc FasterWhisperSTT tuỳ theo --engine.
    Cùng interface STTStrategy nên phần đánh giá bên dưới dùng chung 1
    code path cho cả 2, không cần rẽ nhánh logic đánh giá."""
    if engine == "faster-whisper":
        print(f"Engine: faster-whisper (CTranslate2) | model_path={ct2_path}")
        return FasterWhisperSTT(model_path=ct2_path)

    name = model_name or DEFAULT_MODEL_NAME
    print(f"Engine: PhoWhisperSTT (transformers) | model={name}")
    return PhoWhisperSTT(model_name=name)


# ---------------------------------------------------------------------------
# 3. Chạy transcribe + đo latency
# ---------------------------------------------------------------------------

def run_evaluation(stt: STTStrategy, test_set: List[TestSample]) -> pd.DataFrame:
    rows = []

    for sample in test_set:
        with open(sample.audio_path, "rb") as f:
            audio_bytes = f.read()

        t0 = time.time()
        prediction = stt.transcribe(audio_bytes)
        latency = time.time() - t0

        rows.append({
            "audio_path": sample.audio_path,
            "category": sample.category,
            "ground_truth": sample.ground_truth,
            "prediction": prediction,
            "latency_s": round(latency, 3),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Tính các chỉ số: WER, CER, Exact Match
# ---------------------------------------------------------------------------

def compute_metrics(df: pd.DataFrame) -> pd.DataFrame:
    import jiwer

    def normalize_for_compare(text: str) -> str:
        return text.strip().lower()

    metrics_per_row = []
    for _, row in df.iterrows():
        gt = normalize_for_compare(row["ground_truth"])
        pred = normalize_for_compare(row["prediction"])

        wer = jiwer.wer(gt, pred) if gt else (0.0 if not pred else 1.0)
        cer = jiwer.cer(gt, pred) if gt else (0.0 if not pred else 1.0)
        exact_match = int(gt == pred)

        metrics_per_row.append({"wer": wer, "cer": cer, "exact_match": exact_match})

    metrics_df = pd.DataFrame(metrics_per_row)
    return pd.concat([df.reset_index(drop=True), metrics_df], axis=1)


def summarize(df: pd.DataFrame, engine_label: str) -> dict:
    return {
        "engine": engine_label,
        "avg_wer": round(df["wer"].mean(), 4),
        "avg_cer": round(df["cer"].mean(), 4),
        "exact_match_rate": round(df["exact_match"].mean(), 4),
        "avg_latency_s": round(df["latency_s"].mean(), 3),
        "n_samples": len(df),
    }


# ---------------------------------------------------------------------------
# 5. Chạy đánh giá
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Đánh giá STTStrategy (PhoWhisperSTT hoặc FasterWhisperSTT) trên tập câu lệnh thật."
    )
    parser.add_argument(
        "--engine", type=str, default="phowhisper",
        choices=["phowhisper", "faster-whisper"],
        help="Chọn STTStrategy để đánh giá (mặc định: phowhisper)",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Tên model PhoWhisper (chỉ dùng khi --engine phowhisper, mặc định lấy từ stt_module.py)",
    )
    parser.add_argument(
        "--ct2-path", type=str, default=DEFAULT_CT2_MODEL_PATH,
        help="Đường dẫn model CTranslate2 (chỉ dùng khi --engine faster-whisper)",
    )
    args = parser.parse_args()

    stt = build_stt(args.engine, args.model, args.ct2_path)

    df = run_evaluation(stt, TEST_SET)
    df = compute_metrics(df)

    print("\n--- Chi tiết từng mẫu ---")
    print(df[["audio_path", "category", "ground_truth", "prediction", "wer", "cer", "latency_s"]]
          .to_string(index=False))

    summary = summarize(df, engine_label=args.engine)
    print("\n--- Tổng kết ---")
    for k, v in summary.items():
        print(f"{k}: {v}")

if __name__ == "__main__":
    main()