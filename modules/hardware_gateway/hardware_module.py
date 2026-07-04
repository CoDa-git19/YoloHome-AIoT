from config.settings import ADAFRUIT_IO_USERNAME, ADAFRUIT_IO_KEY
from services.logging_service import log_error
from system_core.observers import Subject
from Adafruit_IO import Client

class HardwareModule(Subject):
    def __init__(self, serial_port: str = ""):
        super().__init__()
        self._serial_port = serial_port

    def execute_command(self, command: dict) -> dict:
        device = command.get("device", "")
        room = command.get("room", "")
        action = command.get("action", "")
        try:
            # TODO: replace with real Serial write to Yolo:Bit
            print(f"[hw] command → device={device!r}, room={room!r}, action={action!r}")
            return {"status": "success", "device": device, "room": room, "action": action}
        except Exception as exc:
            log_error("hw", f"execute_command failed: {exc}")
            return {"status": "error", "message": str(exc)}

    def read_sensors(self) -> dict:
        # TODO: replace with real Serial read from Yolo:Bit
        return {"temperature": 28.5, "humidity": 70.0, "light": 512, "motion": 0}

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
