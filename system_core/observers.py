from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Protocol

logger = logging.getLogger(__name__)

SensorData = dict[str, Any]

# Adafruit IO free tier: 30 data point/phút, tính GỘP trên tất cả feed.
#   Nguồn: https://io.adafruit.com/api/docs/ (kiểm chứng 07/2026)
# read_sensors() trả 4 khóa, vòng cảm biến chạy 2 giây/lần
#   -> 4 x 30 = 120 data point/phút, vượt gấp 4 lần -> lỗi 429, khóa tài khoản.
# 15 giây -> 4 x 4 = 16 data point/phút, an toàn.
ADAFRUIT_MIN_PUBLISH_INTERVAL = 15.0


# =============================================================================
# Interfaces
# =============================================================================

class Observer(ABC):
    """Nhận thông báo khi có dữ liệu cảm biến mới."""

    @abstractmethod
    def update(self, sensor_data: SensorData) -> None:
        raise NotImplementedError


class Subject(ABC):
    """
    Nguồn phát dữ liệu cảm biến.

    HardwareModule kế thừa lớp này. Mỗi vòng đọc cảm biến sẽ notify() cho
    mọi observer đã đăng ký.
    """

    def __init__(self) -> None:
        self._observers: list[Observer] = []

    def attach(self, observer: Observer) -> None:
        if observer not in self._observers:
            self._observers.append(observer)

    def detach(self, observer: Observer) -> None:
        """Gỡ observer. Gỡ cái không tồn tại KHÔNG được ném lỗi."""
        if observer in self._observers:
            self._observers.remove(observer)

    @property
    def observers(self) -> list[Observer]:
        return list(self._observers)

    def notify(self, sensor_data: SensorData) -> list[Exception]:
        """
        Thông báo cho mọi observer.

        Lỗi của MỘT observer không được làm chết các observer còn lại.
        Nếu Adafruit sập, rule engine vẫn phải chạy - nếu không thì cái quạt
        sẽ không bao giờ tự bật.

        Returns:
            Danh sách lỗi đã xảy ra (rỗng nếu mọi observer đều ổn).
        """
        errors: list[Exception] = []

        for observer in list(self._observers):
            try:
                observer.update(sensor_data)
            except Exception as exc:
                errors.append(exc)
                logger.error(
                    "Observer %s thất bại: %s",
                    type(observer).__name__,
                    exc,
                )

        return errors


# =============================================================================
# Cổng vào các service (dùng Protocol để tránh phụ thuộc vòng)
# =============================================================================

class RuleEngine(Protocol):
    def evaluate_sensor_data(
        self,
        current_sensor_data: SensorData,
    ) -> list[dict[str, Any]]:
        ...


class CommandExecutor(Protocol):
    def execute_authorized_command(self, command: dict[str, Any]) -> bool:
        ...


# =============================================================================
# Observers cụ thể
# =============================================================================

class RuleObserver(Observer):
    """
    MẮT XÍCH CÒN THIẾU giữa cảm biến và automation rule.

    Trước đây RuleService.evaluate_sensor_data() không được gọi từ đâu cả,
    nên dù rule đã lưu trong database, cái quạt vẫn không bao giờ tự bật.

    Luồng:
        HardwareModule.poll_sensors()
            -> notify(sensor_data)
                -> RuleObserver.update()
                    -> RuleService.evaluate_sensor_data()   (edge-triggered)
                        -> CommandService.execute_authorized_command()
    """

    def __init__(
        self,
        rule_service: RuleEngine,
        command_executor: CommandExecutor,
    ) -> None:
        self.rule_service = rule_service
        self.command_executor = command_executor
        self.triggered_count = 0

    def update(self, sensor_data: SensorData) -> None:
        actions = self.rule_service.evaluate_sensor_data(sensor_data)

        for action in actions:
            command = {
                "intent": "control_device",
                "action": action["action"],
                "device": action["device"],
                "room": action["room"],
                "face_auth": False,
                "condition": None,
                "response": action.get("response", ""),
            }

            # Phòng thủ nhiều lớp. Rule cần Face Auth lẽ ra đã bị chặn ngay từ
            # lúc tạo (validator -> safety_rule_violation). Nhưng nếu có ai
            # lách được vào database, đây là chốt chặn cuối: rule chạy tự động
            # nên KHÔNG có ai đứng trước camera để xác thực.
            if self._requires_face_auth(command):
                logger.error(
                    "Chặn automation rule cần Face Auth: %s.%s. "
                    "Rule tự chạy thì không thể xác thực khuôn mặt.",
                    command["device"],
                    command["action"],
                )
                continue

            self.command_executor.execute_authorized_command(command)
            self.triggered_count += 1

    @staticmethod
    def _requires_face_auth(command: dict[str, Any]) -> bool:
        from modules.llm_integration.validator import requires_face_auth_by_policy

        return requires_face_auth_by_policy(
            device=command["device"],
            action=command["action"],
        )


class SensorLoggingObserver(Observer):
    """Giữ lịch sử dữ liệu cảm biến cho dashboard và debug."""

    def __init__(self, max_history: int = 100) -> None:
        self.history: deque[SensorData] = deque(maxlen=max_history)

    def update(self, sensor_data: SensorData) -> None:
        self.history.append(dict(sensor_data))
        logger.debug("Sensor: %s", sensor_data)

    @property
    def latest(self) -> SensorData | None:
        return dict(self.history[-1]) if self.history else None


class AdafruitPublisher(Observer):
    """
    Đẩy dữ liệu cảm biến lên Adafruit IO.

    CÓ GIỚI HẠN TẦN SUẤT
    --------------------
    Vòng cảm biến chạy 2 giây/lần, nhưng gói free của Adafruit IO chỉ cho
    khoảng 30 data point/phút. Mỗi lần publish gửi 1 request cho MỖI cảm
    biến (temperature, humidity, light, motion), nên nếu đẩy theo đúng nhịp
    cảm biến thì sẽ vượt giới hạn gấp 4 lần và bị chặn.

    min_interval giữ khoảng cách tối thiểu giữa 2 lần đẩy. Dữ liệu cảm biến
    môi trường thay đổi chậm nên 15 giây là quá đủ cho dashboard.

    Lỗi mạng KHÔNG được làm sập rule engine. Subject.notify() đã cô lập lỗi
    của từng observer nên chuyện đó không xảy ra.
    """

    def __init__(
        self,
        hardware_module: Any,
        min_interval: float = ADAFRUIT_MIN_PUBLISH_INTERVAL,
    ) -> None:
        self.hardware_module = hardware_module
        self.min_interval = min_interval
        self.publish_count = 0
        self.skipped_count = 0

        # monotonic() không bị ảnh hưởng khi đồng hồ hệ thống bị chỉnh.
        # Khởi tạo None để lần publish đầu tiên luôn được chạy ngay.
        self._last_publish: float | None = None

    def update(self, sensor_data: SensorData) -> None:
        now = time.monotonic()
        if self._last_publish is not None:
            if now - self._last_publish < self.min_interval:
                self.skipped_count += 1
                return

        self._last_publish = now
        self.publish_count += 1
        self.hardware_module.publish_to_adafruit(sensor_data)


__all__ = [
    "Observer",
    "Subject",
    "RuleObserver",
    "SensorLoggingObserver",
    "AdafruitPublisher",
    "ADAFRUIT_MIN_PUBLISH_INTERVAL",
]
