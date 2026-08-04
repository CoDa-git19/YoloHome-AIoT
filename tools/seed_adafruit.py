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

VÌ SAO DÙNG MQTT CHỨ KHÔNG PHẢI GÓI adafruit-io
-----------------------------------------------
Bản trước dùng `from Adafruit_IO import Client` (REST). Gói đó KHÔNG cài được
trên Python 3.12+: nó dùng ez_setup.py (cơ chế bootstrap từ ~2014) tải về
setuptools 4.0.1, mà bản đó cần `distutils` - đã bị gỡ khỏi Python 3.12.

    ModuleNotFoundError: No module named 'distutils'
    OSError: Could not build the egg.

Không sửa được, nên gói đã bị bỏ khỏi requirements.txt. Hậu quả: script cũ
luôn dừng ở nhánh `except ImportError` và in ra một gợi ý chắc chắn thất bại
("pip install adafruit-io").

`paho-mqtt` làm được đúng việc đó, đã có sẵn trong requirements, và là chính
thư viện mà hardware_module dùng - đúng nguyên tắc "một tài nguyên, một chủ
sở hữu" trong docs/Design-Principles.md §6.
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

# Hạn mức gói free, tính GỘP trên mọi feed.
# Nguồn: https://io.adafruit.com/api/docs/
FREE_TIER_POINTS_PER_MINUTE = 30


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


def build_mqtt_client():
    """
    Tạo client paho-mqtt đã kết nối io.adafruit.com.

    Trả None kèm thông báo rõ ràng nếu không dựng được - script này là công cụ
    demo, không nên ném traceback vào mặt người dùng.
    """
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("[Seed] paho-mqtt chưa được cài: pip install paho-mqtt")
        return None

    try:
        # paho-mqtt 2.x bắt buộc khai báo phiên bản callback API.
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        # paho-mqtt 1.x (bản đang pin trong requirements.txt).
        client = mqtt.Client()

    client.username_pw_set(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)

    try:
        client.connect("io.adafruit.com", 1883, keepalive=60)
    except Exception as exc:
        print(f"[Seed] Không kết nối được io.adafruit.com: {exc}")
        return None

    client.loop_start()
    return client


def publish(client, feed: str, value: float) -> bool:
    """Trả True nếu data point thật sự được gửi đi."""
    topic = f"{ADAFRUIT_IO_USERNAME}/feeds/{feed}"

    try:
        result = client.publish(topic, str(value))
    except Exception as exc:
        print(f"[Seed] Publish lỗi trên {feed}: {exc}")
        return False

    # paho trả mã rc; 0 là thành công. Không kiểm tra thì mất kết nối giữa
    # chừng sẽ được báo là gửi thành công.
    rc = getattr(result, "rc", 0)
    if rc != 0:
        print(f"[Seed] Publish bị từ chối trên {feed}, mã {rc}")
        return False

    return True


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not ADAFRUIT_IO_USERNAME or not ADAFRUIT_IO_KEY:
        print("[Seed] Thiếu ADAFRUIT_IO_USERNAME / ADAFRUIT_IO_KEY trong .env")
        return 1

    if args.interval <= 0:
        print("[Seed] --interval phải lớn hơn 0.")
        return 1

    points_per_minute = len(FEED_RANGES) * (60.0 / args.interval)
    if points_per_minute > FREE_TIER_POINTS_PER_MINUTE:
        print(
            f"[Seed] interval={args.interval:g}s -> {points_per_minute:.0f} data "
            f"point/min, vượt hạn mức {FREE_TIER_POINTS_PER_MINUTE}/min của gói "
            "free. Dùng interval lớn hơn."
        )
        return 1

    client = build_mqtt_client()
    if client is None:
        return 1

    deadline = time.monotonic() + args.minutes * 60
    print(
        f"[Seed] Publishing every {args.interval:g}s for {args.minutes:g} min "
        f"({points_per_minute:.0f} data point/min). Ctrl+C to stop."
    )

    rounds = 0
    exit_code = 0

    try:
        while time.monotonic() < deadline:
            readings = {feed: make_reading(feed) for feed in FEED_RANGES}

            for feed, value in readings.items():
                if not publish(client, feed, value):
                    exit_code = 1
                    raise SystemExit

            rounds += 1
            print(f"[Seed] round {rounds}: {readings}")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[Seed] Interrupted.")
    except SystemExit:
        pass
    finally:
        # Đóng kết nối, nếu không luồng nền của paho còn sống và tiến trình
        # không thoát hẳn.
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    print(f"[Seed] Done. {rounds} round(s), {rounds * len(FEED_RANGES)} points.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())