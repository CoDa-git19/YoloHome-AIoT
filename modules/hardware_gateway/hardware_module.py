from config.settings import ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY
from services.logging_service import log_error
from system_core.observers import Subject


class HardwareModule(Subject):
    """
    Cổng giao tiếp với Yolo:Bit qua Adafruit IO MQTT.

    HAI CHẾ ĐỘ
    ----------
    - serial_port="" (mặc định) -> CHẾ ĐỘ MÔ PHỎNG. Không kết nối MQTT,
      giữ trạng thái trong bộ nhớ, trả dữ liệu cảm biến từ _sensor_cache.
    - serial_port="<bất kỳ>" -> chế độ thật, kết nối Adafruit IO MQTT.
      Yolo:Bit publish cảm biến lên; backend subscribe để nhận và publish
      lệnh điều khiển xuống.

    Chế độ mô phỏng KHÔNG PHẢI tiện ích tạm bợ, mà là ràng buộc bắt buộc:
    toàn bộ test suite của nhóm chạy trên máy không kết nối Yolo:Bit. Nếu
    __init__ mở kết nối thật thì test của mọi người sẽ gãy.
    Xem tests/pattern/test_observer_pattern.py.

    KIẾN TRÚC ADAFRUIT IO
    ---------------------
    Sensor feeds (Yolo:Bit -> AIO -> Python subscribe):
        home-temperature, home-humidity, home-light, home-motion

    Command feeds (Python publish -> AIO -> Yolo:Bit subscribe):
        home-light-on   : "1" / "0"
        home-fan-speed  : "0".."100"
        home-door       : "open" / "close"
    """

    def __init__(self, serial_port: str = ""):
        super().__init__()
        self._serial_port = serial_port

        # Cờ tường minh để rẽ nhánh simulation vs. real.
        self.simulation_mode = not serial_port

        self._states: dict[tuple[str, str], str] = {}

        # Cache cảm biến — được cập nhật bởi MQTT callback khi có dữ liệu
        # thật từ Yolo:Bit. Giá trị khởi tạo giống mock cũ để simulation
        # không bị ảnh hưởng.
        self._sensor_cache: dict[str, float] = {
            "temperature": 28.5,
            "humidity": 70.0,
            "light": 512.0,
            "motion": 0.0,
        }

        # paho-mqtt client — tạo kiểu Lazy khi chế độ thật lần đầu cần kết nối.
        self._mqtt_client = None

        # Adafruit IO REST client — giữ lại để publish lên AIO dashboard.
        self._aio_client = None

    # Trạng thái mà mỗi action đưa thiết bị về.
    ACTION_TO_STATE = {
        "turn_on": "on",
        "turn_off": "off",
        "open": "open",
        "close": "closed",
    }

    DEFAULT_STATE_BY_DEVICE = {"door": "closed"}
    DEFAULT_STATE = "off"

    # Maps Adafruit IO feed path → sensor key used in _sensor_cache
    FEED_TO_SENSOR_KEY: dict[str, str] = {
        "home-temperature": "temperature",
        "home-humidity":    "humidity",
        "home-light":       "light",
        "home-motion":      "motion",
    }

    # Maps (device, action) → (adafruit_feed_key, payload_string)
    COMMAND_FEED_MAP: dict[tuple[str, str], tuple[str, str]] = {
        ("light", "turn_on"):  ("home-light-on",  "1"),
        ("light", "turn_off"): ("home-light-on",  "0"),
        ("fan",   "turn_on"):  ("home-fan-speed", "70"),
        ("fan",   "turn_off"): ("home-fan-speed", "0"),
        ("door",  "open"):     ("home-door",      "open"),
        ("door",  "close"):    ("home-door",      "close"),
    }

    # =========================================================================
    # Adafruit IO MQTT — real hardware communication
    # =========================================================================

    def _get_mqtt_client(self):
        """
        Trả về paho-mqtt client đã kết nối io.adafruit.com, tạo lazy.

        Trả None khi:
        - simulation_mode=True (serial_port="") — test suite không bao giờ vào đây
        - ADAFRUIT_IO_USERNAME/KEY chưa cấu hình
        - paho-mqtt chưa cài
        - Kết nối thất bại
        """
        if self.simulation_mode:
            return None
        if self._mqtt_client is not None:
            return self._mqtt_client
        if not ADAFRUIT_IO_USERNAME or not ADAFRUIT_IO_KEY:
            return None
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log_error("hw", "paho-mqtt not installed")
            return None
        client = mqtt.Client()
        client.username_pw_set(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)
        client.on_message = self._on_mqtt_message
        try:
            client.connect("io.adafruit.com", 1883, keepalive=60)
            for feed in self.FEED_TO_SENSOR_KEY:
                client.subscribe(f"{ADAFRUIT_IO_USERNAME}/feeds/{feed}")
            client.loop_start()
            self._mqtt_client = client
        except Exception as exc:
            log_error("hw", f"MQTT connect failed: {exc}")
            return None
        return self._mqtt_client

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        feed = msg.topic.split("/feeds/")[-1]
        key = self.FEED_TO_SENSOR_KEY.get(feed)
        if key:
            try:
                self._sensor_cache[key] = float(msg.payload.decode())
            except ValueError:
                log_error("hw", f"Bad payload on {feed}: {msg.payload!r}")

    def _mqtt_publish(self, feed_key: str, payload: str) -> None:
        client = self._get_mqtt_client()
        if client is None:
            return
        topic = f"{ADAFRUIT_IO_USERNAME}/feeds/{feed_key}"
        try:
            client.publish(topic, payload)
        except Exception as exc:
            log_error("hw", f"MQTT publish failed on {feed_key}: {exc}")
            self._mqtt_client = None

    def execute_command(self, command: dict) -> dict:
        """
        HỢP ĐỒNG với CommandService:

        - Luôn trả {"status": "success" | "error", ...}
        - Với action="get_status", BẮT BUỘC kèm "state":
              "on" | "off" | "open" | "closed"
          Thiếu "state" thì người dùng sẽ nhận
          "không xác định được trạng thái".

        Hiện đang giữ trạng thái trong bộ nhớ. Khi nối Yolo:Bit thật, thay
        phần đọc/ghi bằng Serial nhưng PHẢI giữ nguyên định dạng trả về.
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
                feed_info = self.COMMAND_FEED_MAP.get((device, action))
                if feed_info:
                    feed_key, payload = feed_info
                    self._mqtt_publish(feed_key, payload)

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
            log_error("hw", f"execute_command failed: {exc}")
            return {"status": "error", "message": str(exc)}

    def read_sensors(self) -> dict:
        """
        Đọc cảm biến.

        4 KHÓA NÀY LÀ HỢP ĐỒNG. RuleService dựa vào chúng để khớp với
        condition["sensor"]. Đổi tên khóa khi nối phần cứng thật sẽ làm MỌI
        automation rule ngừng kích hoạt mà không hề báo lỗi.

        Trong chế độ thật, Yolo:Bit đẩy giá trị lên Adafruit IO; paho-mqtt
        callback _on_mqtt_message() cập nhật _sensor_cache trong nền; hàm
        này chỉ trả bản sao hiện tại của cache đó.
        """
        if not self.simulation_mode:
            self._get_mqtt_client()
        return dict(self._sensor_cache)

    def poll_sensors(self) -> dict:
        """
        Đọc cảm biến MỘT lần rồi thông báo cho mọi observer.

        Đây là nhịp tim của hệ thống. Không gọi hàm này thì automation rule
        không bao giờ chạy - dù rule đã nằm sẵn trong database.

        Gọi định kỳ (ví dụ mỗi 2 giây) từ main.py hoặc scheduler.
        """
        sensor_data = self.read_sensors()
        self.notify(sensor_data)

        return sensor_data

    # =========================================================================
    # Adafruit IO
    # =========================================================================

    def _get_aio_client(self):
        """
        Trả về Adafruit IO client, tạo lazy và dùng lại.

        Trả None khi chưa cấu hình credentials hoặc chưa cài package - khi
        đó publish_to_adafruit() bỏ qua im lặng thay vì spam error_log.
        """
        if self._aio_client is not None:
            return self._aio_client

        if not ADAFRUIT_IO_USERNAME or not ADAFRUIT_IO_KEY:
            return None

        try:
            from Adafruit_IO import Client
        except ImportError:
            log_error("adafruit", "Adafruit_IO package not installed")
            return None

        try:
            self._aio_client = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)
        except Exception as exc:
            log_error("adafruit", f"Failed to create Adafruit IO client: {exc}")
            return None

        return self._aio_client

    def publish_to_adafruit(self, data: dict) -> None:
        """
        Đẩy dữ liệu cảm biến lên Adafruit IO. CHỈ dùng thủ công.

        ĐỪNG nối hàm này vào observer nào. Gói free cho 30 data point/phút
        TÍNH GỘP mọi feed (https://io.adafruit.com/api/docs/), mà Yolo:Bit
        đã publish 4 feed sensor mỗi 10 giây -> 24 data point/phút. Hơn nữa
        4 feed hàm này ghi vào chính là 4 feed Yolo:Bit sở hữu và backend
        đang subscribe -> chỉ vọng lại dữ liệu vừa đọc.
        Lưu lịch sử cảm biến bằng SensorPersistObserver (ghi SQLite).
        """
        aio = self._get_aio_client()
        if aio is None:
            return

        for key, value in data.items():
            feed_key = f"home-{key}"
            try:
                aio.send_data(feed_key, value)
            except Exception as exc:
                log_error("adafruit", f"Failed to publish to feed '{feed_key}': {exc}")

                # Kết nối có thể đã hỏng (token hết hạn, đổi mạng).
                # Bỏ client để lần sau tạo lại thay vì hỏng vĩnh viễn.
                self._aio_client = None
                return
