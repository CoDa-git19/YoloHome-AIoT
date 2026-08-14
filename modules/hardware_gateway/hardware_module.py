"""
Hardware gateway — cầu nối giữa backend và Yolo:Bit qua Adafruit IO MQTT.

OWNER: Hardware module.

HAI CHẾ ĐỘ
----------
- mode="simulation" (mặc định) -> giữ trạng thái trong RAM, KHÔNG nối mạng,
  cảm biến trả giá trị mô phỏng cố định.
- mode="real" -> subscribe feed cảm biến của Yolo:Bit và publish lệnh xuống.

Chế độ mô phỏng KHÔNG PHẢI tiện ích tạm bợ mà là ràng buộc bắt buộc: toàn bộ
test suite chạy trên máy không cắm Yolo:Bit. Nếu __init__ mở kết nối thật thì
test của cả nhóm sẽ gãy.

KIẾN TRÚC ADAFRUIT IO
---------------------
Sensor feeds (Yolo:Bit publish -> backend subscribe):
    home-temperature, home-humidity, home-light, home-motion

Command feeds (backend publish -> Yolo:Bit subscribe):
    home-light-living, home-light-bed     : "1" / "0"
    home-fan-living,   home-fan-bed       : "0".."100"
    home-door                             : "open" / "close"

Tổng 9 feed. Gói free giới hạn 10 feed nên còn đúng 1 chỗ trống — thêm phòng
mới vào device_registry.json sẽ cần xem lại chỗ này.

LƯU Ý VỀ TÊN: "home-light" là cảm biến ÁNH SÁNG, còn "home-light-living" là
lệnh bật/tắt ĐÈN phòng khách. Hai thứ khác nhau dù tên gần giống.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from config.settings import ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY
from services.logging_service import log_error
from system_core.observers import Subject


# Khoảng cách tối thiểu giữa hai lần thử kết nối MQTT (giây).
#
# Không có nó thì mất mạng sẽ khiến mỗi vòng đọc cảm biến (2 giây/lần) gọi
# client.connect() - một lời gọi BLOCKING - và vòng cảm biến bị treo theo
# timeout TCP. Automation rule đứng theo luôn.
MQTT_RECONNECT_MIN_INTERVAL = 30.0

# Dữ liệu cảm biến cũ hơn ngần này giây thì coi là KHÔNG CÒN ĐÁNG TIN.
# Yolo:Bit publish mỗi 10 giây, nên 60 giây là đã lỡ 6 nhịp liên tiếp.
SENSOR_STALE_AFTER_SECONDS = 60.0


class HardwareModule(Subject):
    """Thực thi lệnh điều khiển và đọc cảm biến."""

    # Trạng thái mà mỗi action đưa thiết bị về.
    ACTION_TO_STATE = {
        "turn_on": "on",
        "turn_off": "off",
        "open": "open",
        "close": "closed",
    }

    DEFAULT_STATE_BY_DEVICE = {"door": "closed"}
    DEFAULT_STATE = "off"

    # Feed cảm biến -> khoá trong _sensor_cache.
    FEED_TO_SENSOR_KEY: dict[str, str] = {
        "home-temperature": "temperature",
        "home-humidity": "humidity",
        "home-light": "light",
        "home-motion": "motion",
    }

    # (room, device, action) -> (feed_key, payload)
    #
    # PHẢI CÓ room TRONG KHOÁ.
    #
    # Bản trước khoá theo (device, action), nên "bật đèn phòng ngủ" và "bật đèn
    # phòng khách" cùng publish home-light-on="1" -> HAI ĐÈN SÁNG CÙNG LÚC.
    # Toàn bộ phần multi-room trong prompt, validator và báo cáo mô tả một hệ
    # thống mà phần cứng không thực hiện được.
    #
    # Danh sách này phải phủ HẾT tổ hợp trong config/device_registry.json
    # (trừ get_status - action đó không publish gì). Thiếu một tổ hợp thì
    # execute_command() trả "error" thay vì im lặng báo thành công.
    COMMAND_FEED_MAP: dict[tuple[str, str, str], tuple[str, str]] = {
        ("living_room", "light", "turn_on"): ("home-light-living", "1"),
        ("living_room", "light", "turn_off"): ("home-light-living", "0"),
        ("living_room", "fan", "turn_on"): ("home-fan-living", "70"),
        ("living_room", "fan", "turn_off"): ("home-fan-living", "0"),
        ("bedroom", "light", "turn_on"): ("home-light-bed", "1"),
        ("bedroom", "light", "turn_off"): ("home-light-bed", "0"),
        ("bedroom", "fan", "turn_on"): ("home-fan-bed", "70"),
        ("bedroom", "fan", "turn_off"): ("home-fan-bed", "0"),
        ("main_door", "door", "open"): ("home-door", "open"),
        ("main_door", "door", "close"): ("home-door", "close"),
    }

    # Giá trị dùng cho chế độ mô phỏng. KHÔNG dùng ở chế độ thật - xem
    # _sensor_cache trong __init__.
    SIMULATED_SENSORS: dict[str, float] = {
        "temperature": 28.5,
        "humidity": 70.0,
        "light": 512.0,
        "motion": 0.0,
    }

    def __init__(self, mode: str = "simulation") -> None:
        """
        Args:
            mode: "simulation" hoặc "real".

                Tham số cũ tên `serial_port` đã bỏ: hệ thống không còn dùng
                cổng Serial ở đâu cả, toàn bộ giao tiếp qua MQTT. Giữ cái tên
                đó khiến người đọc tưởng có một đường Serial nào đó tồn tại,
                và khiến `HardwareModule()` mặc định rơi vào mô phỏng mà không
                ai nhận ra.
        """
        super().__init__()

        if mode not in {"simulation", "real"}:
            raise ValueError(
                f"mode={mode!r} không hợp lệ. Dùng 'simulation' hoặc 'real'."
            )

        self.mode = mode
        self.simulation_mode = mode == "simulation"

        self._states: dict[tuple[str, str], str] = {}

        # Ở chế độ THẬT, cache bắt đầu RỖNG.
        #
        # Bản trước khởi tạo bằng 28.5 / 70 / 512 / 0 - giá trị mô phỏng. Ở chế
        # độ thật, trước khi MQTT về message đầu tiên, rule "nhiệt độ trên 25
        # thì bật quạt" sẽ KÍCH HOẠT NGAY LÚC BOOT bằng dữ liệu không có thật.
        self._sensor_cache: dict[str, float] = (
            dict(self.SIMULATED_SENSORS) if self.simulation_mode else {}
        )

        # Thời điểm nhận được giá trị gần nhất cho từng cảm biến.
        self._sensor_updated_at: dict[str, float] = {}

        self._mqtt_client = None
        self._mqtt_lock = threading.Lock()
        self._last_connect_attempt = 0.0

    # =========================================================================
    # MQTT
    # =========================================================================

    def _build_mqtt_client(self):
        """Tạo client paho-mqtt, tương thích cả bản 1.x và 2.x."""
        import paho.mqtt.client as mqtt

        try:
            # paho-mqtt 2.x bắt buộc khai báo phiên bản callback API.
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except AttributeError:
            # paho-mqtt 1.x (bản đang pin trong requirements.txt).
            return mqtt.Client()

    def _get_mqtt_client(self):
        """
        Trả client đã kết nối io.adafruit.com, tạo lazy.

        Trả None khi: chế độ mô phỏng, thiếu credential, chưa cài paho-mqtt,
        hoặc kết nối thất bại.
        """
        if self.simulation_mode:
            return None

        if self._mqtt_client is not None:
            return self._mqtt_client

        if not ADAFRUIT_IO_USERNAME or not ADAFRUIT_IO_KEY:
            return None

        # Chặn retry dồn dập. Mất mạng thì mỗi vòng cảm biến sẽ gọi connect()
        # blocking và kéo cả vòng lặp đứng theo timeout TCP.
        now = time.monotonic()
        if now - self._last_connect_attempt < MQTT_RECONNECT_MIN_INTERVAL:
            return None

        with self._mqtt_lock:
            if self._mqtt_client is not None:
                return self._mqtt_client

            self._last_connect_attempt = now

            try:
                client = self._build_mqtt_client()
            except ImportError:
                log_error("hw", "paho-mqtt chưa được cài")
                return None

            client.username_pw_set(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)
            client.on_connect = self._on_mqtt_connect
            client.on_message = self._on_mqtt_message
            client.on_disconnect = self._on_mqtt_disconnect

            try:
                client.connect("io.adafruit.com", 1883, keepalive=60)
                client.loop_start()
                self._mqtt_client = client
            except Exception as exc:
                log_error("hw", f"MQTT connect thất bại: {exc}")
                return None

        return self._mqtt_client

    def _on_mqtt_connect(self, client, userdata, flags, rc, *args) -> None:
        """
        SUBSCRIBE PHẢI NẰM Ở ĐÂY, KHÔNG PHẢI SAU connect().

        loop_start() có tự động reconnect khi rớt mạng, nhưng paho KHÔNG tự
        subscribe lại. Bản trước gọi subscribe() một lần ngay sau connect(),
        nên chỉ cần rớt wifi 3 giây là từ đó về sau không nhận được message
        nào nữa - KHÔNG lỗi, KHÔNG log, _sensor_cache giữ giá trị cũ vĩnh viễn
        và rule engine ra quyết định trên dữ liệu chết.

        on_connect được gọi lại sau MỖI lần kết nối, kể cả reconnect tự động.
        """
        if rc != 0:
            log_error("hw", f"MQTT từ chối kết nối, mã {rc}")
            return

        for feed in self.FEED_TO_SENSOR_KEY:
            client.subscribe(f"{ADAFRUIT_IO_USERNAME}/feeds/{feed}")

        print(f"[Hardware] MQTT đã kết nối, subscribe {len(self.FEED_TO_SENSOR_KEY)} feed cảm biến.")

    def _on_mqtt_disconnect(self, client, userdata, rc, *args) -> None:
        if rc != 0:
            log_error("hw", f"MQTT mất kết nối ngoài ý muốn (mã {rc}), đang thử lại")

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        feed = msg.topic.split("/feeds/")[-1]
        key = self.FEED_TO_SENSOR_KEY.get(feed)

        if key is None:
            return

        try:
            self._sensor_cache[key] = float(msg.payload.decode())
            self._sensor_updated_at[key] = time.monotonic()
        except (ValueError, UnicodeDecodeError):
            log_error("hw", f"Payload không hợp lệ trên {feed}: {msg.payload!r}")

    def _mqtt_publish(self, feed_key: str, payload: str) -> bool:
        """Trả True nếu đã gửi được lệnh xuống thiết bị."""
        client = self._get_mqtt_client()
        if client is None:
            return False

        topic = f"{ADAFRUIT_IO_USERNAME}/feeds/{feed_key}"

        try:
            result = client.publish(topic, payload)
        except Exception as exc:
            log_error("hw", f"MQTT publish lỗi trên {feed_key}: {exc}")
            self._mqtt_client = None
            return False

        # paho trả mã rc; 0 là thành công. Không kiểm tra thì mất kết nối giữa
        # chừng sẽ được báo là gửi thành công.
        rc = getattr(result, "rc", 0)
        if rc != 0:
            log_error("hw", f"MQTT publish bị từ chối trên {feed_key}, mã {rc}")
            return False

        return True

    def stop(self) -> None:
        """Đóng kết nối MQTT. Gọi khi thoát chương trình."""
        client = self._mqtt_client
        self._mqtt_client = None

        if client is None:
            return

        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    # =========================================================================
    # Thực thi lệnh
    # =========================================================================

    def execute_command(self, command: dict) -> dict:
        """
        HỢP ĐỒNG với CommandService:

        - Luôn trả {"status": "success" | "error", ...}
        - Với action="get_status", BẮT BUỘC kèm "state":
              "on" | "off" | "open" | "closed"
          Thiếu "state" thì người dùng sẽ nhận "không xác định được trạng thái".
        """
        device = command.get("device", "")
        room = command.get("room", "")
        action = command.get("action", "")
        key = (room, device)

        try:
            if action == "get_status":
                state = self._states.get(
                    key,
                    self.DEFAULT_STATE_BY_DEVICE.get(device, self.DEFAULT_STATE),
                )
                return {
                    "status": "success",
                    "device": device,
                    "room": room,
                    "action": action,
                    "state": state,
                }

            if not self.simulation_mode:
                feed_info = self.COMMAND_FEED_MAP.get((room, device, action))

                if feed_info is None:
                    # KHÔNG được im lặng bỏ qua rồi báo thành công.
                    #
                    # Bản trước làm đúng như vậy: tổ hợp không có trong feed map
                    # thì không publish gì, nhưng vẫn cập nhật _states và trả
                    # "success". Người dùng nghe "Đã bật đèn phòng ngủ" trong
                    # khi không có tín hiệu nào rời khỏi máy.
                    message = (
                        f"Chưa có feed cho ({room}, {device}, {action}). "
                        "Bổ sung vào COMMAND_FEED_MAP."
                    )
                    log_error("hw", message)
                    return {"status": "error", "message": message}

                feed_key, payload = feed_info

                if not self._mqtt_publish(feed_key, payload):
                    return {
                        "status": "error",
                        "message": f"Không gửi được lệnh tới {feed_key}.",
                    }

            new_state = self.ACTION_TO_STATE.get(action)
            if new_state is not None:
                self._states[key] = new_state

            return {
                "status": "success",
                "device": device,
                "room": room,
                "action": action,
                "state": self._states.get(key),
            }

        except Exception as exc:
            log_error("hw", f"execute_command thất bại: {exc}")
            return {"status": "error", "message": str(exc)}

    # =========================================================================
    # Đọc cảm biến
    # =========================================================================

    def read_sensors(self) -> dict:
        """
        Đọc cảm biến.

        BỐN KHOÁ NÀY LÀ HỢP ĐỒNG: temperature, humidity, light, motion.
        RuleService khớp chúng với condition["sensor"]. Đổi tên khoá sẽ làm MỌI
        automation rule ngừng kích hoạt mà không hề báo lỗi.

        Ở chế độ THẬT, hàm chỉ trả những cảm biến ĐÃ THỰC SỰ nhận được dữ liệu
        và dữ liệu đó chưa quá cũ. Cảm biến chưa về hoặc đã ngừng gửi thì KHÔNG
        xuất hiện trong kết quả - rule dựa vào nó sẽ không khớp, thay vì khớp
        bằng một con số bịa.
        """
        if self.simulation_mode:
            return dict(self._sensor_cache)

        # Chạm vào client để kích hoạt kết nối lazy nếu chưa có.
        self._get_mqtt_client()

        now = time.monotonic()
        fresh: dict[str, float] = {}

        for key, value in self._sensor_cache.items():
            updated_at = self._sensor_updated_at.get(key)

            if updated_at is None:
                continue

            if now - updated_at > SENSOR_STALE_AFTER_SECONDS:
                continue

            fresh[key] = value

        return fresh

    def sensor_ages(self) -> dict[str, float]:
        """
        Số giây kể từ lần cập nhật gần nhất của mỗi cảm biến.

        Dùng cho dashboard và chẩn đoán: cảm biến biến mất khỏi read_sensors()
        thì đây là chỗ cho biết nó ngừng gửi từ bao giờ.
        """
        now = time.monotonic()
        return {
            key: now - updated_at
            for key, updated_at in self._sensor_updated_at.items()
        }

    def poll_sensors(self) -> dict:
        """
        Đọc cảm biến MỘT lần rồi thông báo cho mọi observer.

        Đây là nhịp tim của hệ thống. Không gọi hàm này thì automation rule
        không bao giờ chạy - dù rule đã nằm sẵn trong database.

        Gọi định kỳ (ví dụ mỗi 2 giây) từ main.py hoặc scheduler.
        """
        sensor_data = self.read_sensors()

        # Chế độ thật mà chưa có dữ liệu nào: không notify. Đẩy dict rỗng
        # xuống observer chỉ tạo ra dòng sensor_log trống và làm rule engine
        # chạy không.
        if sensor_data:
            self.notify(sensor_data)

        return sensor_data

    # =========================================================================
    # Đẩy dữ liệu lên Adafruit IO
    # =========================================================================

    def publish_to_adafruit(self, data: dict) -> None:
        """
        Đẩy dữ liệu lên Adafruit IO qua MQTT. CHỈ dùng thủ công.

        ĐỪNG nối hàm này vào observer nào. Gói free cho 30 data point/phút TÍNH
        GỘP mọi feed (https://io.adafruit.com/api/docs/), mà Yolo:Bit đã publish
        4 feed cảm biến mỗi 10 giây -> 24 data point/phút. Hơn nữa 4 feed hàm
        này ghi vào chính là 4 feed Yolo:Bit sở hữu và backend đang subscribe
        -> chỉ vọng lại dữ liệu vừa đọc.

        Lưu lịch sử cảm biến bằng SensorPersistObserver (ghi SQLite).

        Bản trước dùng gói `adafruit-io` (REST). Gói đó KHÔNG cài được trên
        Python 3.12+ vì ez_setup.py cần distutils đã bị gỡ. Dùng chính client
        MQTT đang có sẵn thì vừa gỡ được phụ thuộc, vừa đúng nguyên tắc "một
        tài nguyên, một chủ sở hữu".
        """
        if self.simulation_mode:
            log_error("hw", "publish_to_adafruit() bị gọi ở chế độ mô phỏng - bỏ qua")
            return

        for key, value in data.items():
            self._mqtt_publish(f"home-{key}", str(value))


__all__ = [
    "HardwareModule",
    "MQTT_RECONNECT_MIN_INTERVAL",
    "SENSOR_STALE_AFTER_SECONDS",
]