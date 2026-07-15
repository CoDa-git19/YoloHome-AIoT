from config.settings import ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY
from services.logging_service import log_error
from system_core.observers import Subject
from Adafruit_IO import Client

class HardwareModule(Subject):
    def __init__(self, serial_port: str = ""):
        super().__init__()
        self._serial_port = serial_port
        self._states: dict[tuple[str, str], str] = {}

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

    def publish_to_adafruit(self, data: dict) -> None:
        try:
            aio = Client(ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY)
            for key, value in data.items():
                feed_key = f"home-{key}"
                try:
                    aio.send_data(feed_key, value)
                except Exception as exc:
                    log_error("adafruit", f"Failed to publish to feed '{feed_key}': {exc}")
        except ImportError:
            log_error("adafruit", "Adafruit_IO package not installed")
        except Exception as exc:
            log_error("adafruit", f"publish_to_adafruit error: {exc}")