from config.settings import ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY
from services.logging_service import log_error
from system_core.observers import Subject


class HardwareModule(Subject):
    """
    Cổng giao tiếp với Yolo:Bit.

    HAI CHẾ ĐỘ
    ----------
    - serial_port="" (mặc định) -> CHẾ ĐỘ MÔ PHỎNG. Không mở cổng Serial,
      giữ trạng thái trong bộ nhớ, trả dữ liệu cảm biến cố định.
    - serial_port="COM3" (ví dụ) -> chế độ thật, đọc/ghi qua Serial.

    Chế độ mô phỏng KHÔNG PHẢI tiện ích tạm bợ, mà là ràng buộc bắt buộc:
    toàn bộ test suite của nhóm chạy trên máy không cắm Yolo:Bit. Nếu
    __init__ mở cổng Serial thật thì test của mọi người sẽ gãy.
    Xem tests/pattern/test_observer_pattern.py.
    """

    def __init__(self, serial_port: str = ""):
        super().__init__()
        self._serial_port = serial_port

        # Cờ tường minh để phần code Serial sau này rẽ nhánh.
        self.simulation_mode = not serial_port

        self._states: dict[tuple[str, str], str] = {}

        # Adafruit IO client được tạo LƯỜI và dùng lại.
        # Nếu tạo mới mỗi lần publish, chạy 24/7 sẽ tạo hàng nghìn client.
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
                # TODO: đọc trạng thái thật từ Yolo:Bit qua Serial
                state = self._states.get(
                    key,
                    self.DEFAULT_STATE_BY_DEVICE.get(device, self.DEFAULT_STATE),
                )
                print(f"[hw] status  → device={device!r}, room={room!r}, state={state!r}")
                return {
                    "status": "success",
                    "device": device,
                    "room": room,
                    "action": action,
                    "state": state,
                }

            # TODO: replace with real Serial write to Yolo:Bit
            print(f"[hw] command → device={device!r}, room={room!r}, action={action!r}")

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
        condition["sensor"]. Đổi tên khóa khi nối Serial thật sẽ làm MỌI
        automation rule ngừng kích hoạt mà không hề báo lỗi.
        """
        # TODO: replace with real Serial read from Yolo:Bit
        return {"temperature": 28.5, "humidity": 70.0, "light": 512, "motion": 0}

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
        Trả về Adafruit IO client, tạo lười và dùng lại.

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
        Đẩy dữ liệu cảm biến lên Adafruit IO.

        KHÔNG gọi trực tiếp từ vòng cảm biến. Dùng AdafruitPublisher trong
        system_core/observers.py, vì nó có giới hạn tần suất. Gói free chỉ
        cho 30 data point/phút TÍNH GỘP mọi feed, mà mỗi lần gọi hàm này
        gửi 1 request cho MỖI cảm biến.
        Nguồn: https://io.adafruit.com/api/docs/
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
