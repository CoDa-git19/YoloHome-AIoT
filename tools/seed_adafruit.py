"""
Bơm dữ liệu cảm biến giả lên Adafruit IO để demo chay (không cần Yolo:Bit).

    python -m tools.seed_adafruit --minutes 5

VÌ SAO LÀ SCRIPT GỌI TAY, KHÔNG PHẢI OBSERVER
---------------------------------------------
Gói free chỉ cho 30 data point/phút TÍNH GỘP mọi feed. Yolo:Bit đã dùng 24
(4 feed x 6 lần/phút), nên một observer chạy nền sẽ vượt hạn mức và kéo theo
429 khóa CẢ TÀI KHOẢN - chặn luôn dữ liệu thật của Yolo:Bit.

Script này chỉ chạy khi bạn gọi, và mặc định giãn 20 giây/lần (3 lần/phút x
4 feed = 12 data point/phút). ĐỪNG chạy cùng lúc với Yolo:Bit thật: hai
nguồn ghi chung một feed vừa vượt hạn mức vừa làm nhiễu dữ liệu.
"""

from __future__ import annotations

import argparse
import random
import time

from config.settings import ADAFRUIT_IO_KEY, ADAFRUIT_IO_USERNAME

# 4 feed cảm biến mà Yolo:Bit sở hữu, kèm khoảng giá trị hợp lý để biểu đồ
# demo trông giống dữ liệu thật thay vì một đường kẻ ngang.
FEED_RANGES = {
    "home-temperature": (26.0, 34.0),
    "home-humidity": (55.0, 85.0),
    "home-light": (100.0, 900.0),
    "home-motion": (0.0, 1.0),
}

DEFAULT_INTERVAL = 20.0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.seed_adafruit",
        description="Publish fake sensor data to Adafruit IO for demos.",
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=5.0,
        help="How long to keep publishing.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=(
            "Seconds between rounds. Each round sends 4 data points; "
            "the free tier allows 30 per minute across all feeds."
        ),
    )
    return parser


def make_reading(feed: str) -> float:
    low, high = FEED_RANGES[feed]

    if feed == "home-motion":
        # Chuyển động là 0/1, không phải số thực.
        return float(random.random() < 0.25)

    return round(random.uniform(low, high), 1)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not ADAFRUIT_IO_USERNAME or not ADAFRUIT_IO_KEY:
        print("[Seed] ADAFRUIT_IO_USERNAME / ADAFRUIT_IO_KEY missing in .env")
        return 1

    points_per_minute = len(FEED_RANGES) * (60.0 / args.interval)
    if points_per_minute > 30:
        print(
            f"[Seed] interval={args.interval}s -> {points_per_minute:.0f} data "
            "point/min, over the 30/min free tier limit. Use a larger interval."
        )
        return 1

    try:
        from Adafruit_IO import Client
    except ImportError:
        print("[Seed] Adafruit_IO not installed: pip install adafruit-io")
        return 1

    client = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)

    deadline = time.monotonic() + args.minutes * 60
    print(
        f"[Seed] Publishing every {args.interval:g}s for {args.minutes:g} min "
        f"({points_per_minute:.0f} data point/min). Ctrl+C to stop."
    )

    rounds = 0
    try:
        while time.monotonic() < deadline:
            readings = {feed: make_reading(feed) for feed in FEED_RANGES}

            for feed, value in readings.items():
                try:
                    client.send_data(feed, value)
                except Exception as exc:
                    print(f"[Seed] Failed on {feed}: {exc}")
                    return 1

            rounds += 1
            print(f"[Seed] round {rounds}: {readings}")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[Seed] Interrupted.")

    print(f"[Seed] Done. {rounds} round(s), {rounds * len(FEED_RANGES)} points.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
