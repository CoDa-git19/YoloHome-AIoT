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
# Yolo:Bit đã publish 4 feed sensor mỗi 10 giây -> 24 data point/phút.
# Backend đẩy thêm vừa vượt hạn mức, vừa chỉ vọng lại chính feed vừa đọc
#   -> Lưu lịch sử bằng SensorPersistObserver.


def _record(module: str, message: str) -> None:
    """
    Ghi lỗi ra CẢ HAI kênh: console và bảng error_log.

    Mọi lỗi trong file này đều thuộc loại hỏng âm thầm - observer chết thì
    automation ngừng chạy mà không ai biết, lệnh hẹn giờ hỏng thì người dùng
    đã được báo "đã hẹn giờ xong" rồi. Chỉ ghi ra console là không đủ, vì
    không entry point nào cấu hình logging và dòng log mất khi tắt terminal.

    Import cục bộ để system_core không phụ thuộc services lúc import module,
    và bọc try/except vì hàm này được gọi từ trong các khối xử lý lỗi - nó
    tuyệt đối không được ném thêm lỗi mới.
    """
    logger.error(message)

    try:
        from services.logging_service import log_error

        log_error(module, message)
    except Exception as exc:  # noqa: BLE001
        logger.error("Không ghi được error_log: %s", exc)


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

        Cô lập lỗi mà không ghi lại thì đổi một kiểu hỏng âm thầm lấy một kiểu
        khác: SensorPersistObserver chết thì sensor_log ngừng ghi vĩnh viễn và
        không để lại dấu vết nào. Vì vậy mỗi lỗi đều vào error_log.

        Returns:
            Danh sách lỗi đã xảy ra (rỗng nếu mọi observer đều ổn).
        """
        errors: list[Exception] = []

        for observer in list(self._observers):
            try:
                observer.update(sensor_data)
            except Exception as exc:
                errors.append(exc)
                _record(
                    "observer",
                    f"Observer {type(observer).__name__} thất bại: {exc}",
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
            #
            # Chốt này kích hoạt nghĩa là có thứ đã ghi thẳng vào database -
            # một sự kiện an ninh, nên nó vào error_log dưới tag security_policy
            # cùng chỗ với các quyết định chặn của CommandService.
            if self._requires_face_auth(command):
                _record(
                    "security_policy",
                    f"Chặn automation rule cần Face Auth: "
                    f"{command['device']}.{command['action']}. "
                    "Rule tự chạy thì không thể xác thực khuôn mặt.",
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


class ScheduleObserver(Observer):
    """
    Chạy các lệnh hẹn giờ đã tới hạn.

    VÌ SAO GẮN VÀO VÒNG CẢM BIẾN
    ----------------------------
    Lệnh hẹn giờ cần một cái đồng hồ. Hệ thống đã có sẵn một nhịp đập đều đặn:
    poll_sensors() chạy mỗi 2 giây. Dựng thêm một luồng hẹn giờ riêng là thêm
    một nơi có thể chết âm thầm, thêm một chỗ phải dọn khi thoát, và thêm một
    thứ để quên attach.

    Đổi lại: độ chính xác chỉ tới mức chu kỳ poll. Lịch đặt 60 giây có thể chạy
    ở giây thứ 60 hoặc 62. Với lệnh nhà thông minh thì không đáng kể.

    Observer này KHÔNG dùng tới sensor_data. Nó nhận tham số đó vì Observer là
    một interface chung, và nó cần nhịp đập chứ không cần dữ liệu.

    CHỐT CHẶN XÁC THỰC
    ------------------
    Giống RuleObserver: lệnh hẹn giờ chạy khi KHÔNG có ai đứng trước camera,
    nên hành động cần Face Auth không bao giờ được chạy từ đây. Validator đã
    chặn lúc tạo; đây là lớp thứ hai, cho trường hợp có ai đó ghi thẳng vào
    bảng schedule.

    MỌI LỖI Ở ĐÂY ĐỀU ÂM THẦM
    -------------------------
    Người dùng đã nghe "đã hẹn giờ xong" từ trước đó rất lâu. Lịch không chạy
    được thì không có ai để báo, nên toàn bộ nhánh lỗi đều ghi error_log.
    """

    def __init__(
        self,
        command_executor: CommandExecutor,
        schedule_reader: Any = None,
    ) -> None:
        self.command_executor = command_executor

        # Tiêm được để test không cần database.
        if schedule_reader is None:
            from services.logging_service import get_schedules

            schedule_reader = get_schedules

        self.schedule_reader = schedule_reader
        self.executed_count = 0
        self.blocked_count = 0

    def update(self, sensor_data: SensorData) -> None:
        for row in self._due_schedules():
            command = self._command_from(row)
            if command is None:
                continue

            if RuleObserver._requires_face_auth(command):
                self.blocked_count += 1
                _record(
                    "security_policy",
                    f"Chặn lệnh hẹn giờ cần Face Auth: "
                    f"{command.get('device')}.{command.get('action')}. "
                    "Lệnh hẹn giờ chạy tự động nên không thể xác thực khuôn mặt.",
                )
                continue

            try:
                ok = self.command_executor.execute_authorized_command(command)
            except Exception as exc:
                _record(
                    "scheduler",
                    f"Không chạy được lệnh hẹn giờ id={row.get('id')}: {exc}",
                )
                continue

            if ok:
                self.executed_count += 1

    def _due_schedules(self) -> list[dict[str, Any]]:
        """
        get_schedules() vừa ĐỌC vừa ĐÁNH DẤU đã dùng trong một giao dịch, nên
        một lịch không thể chạy hai lần kể cả khi hai vòng poll chồng nhau.
        """
        try:
            return list(self.schedule_reader() or [])
        except Exception as exc:
            _record("scheduler", f"Không đọc được lịch hẹn giờ: {exc}")
            return []

    @staticmethod
    def _command_from(row: dict[str, Any]) -> dict[str, Any] | None:
        import json

        raw = row.get("json_cmd")

        try:
            command = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError) as exc:
            _record("scheduler", f"Lịch id={row.get('id')} có json_cmd hỏng: {exc}")
            return None

        if not command.get("device") or not command.get("action"):
            _record("scheduler", f"Lịch id={row.get('id')} thiếu device hoặc action")
            return None

        # Chạy như một lệnh điều khiển bình thường: bảng schedule lưu ý định,
        # còn thứ đưa xuống phần cứng phải là một lệnh control_device hợp lệ.
        return {
            "intent": "control_device",
            "action": command["action"],
            "device": command["device"],
            "room": command.get("room"),
            "face_auth": False,
            "condition": None,
            "response": command.get("response", ""),
        }


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


SENSOR_PERSIST_INTERVAL = 30.0


class SensorPersistObserver(Observer):
    """
    Ghi sensor_log xuống SQLite.

    CÓ GIỚI HẠN TẦN SUẤT
    --------------------
    Vòng cảm biến chạy 2 giây/lần và read_sensors() trả 4 khóa, tức là
    120 dòng/phút nếu ghi mỗi lần. Dữ liệu môi trường thay đổi chậm nên
    30 giây là quá đủ cho biểu đồ dashboard, và giữ DB ở mức 8 dòng/phút.

    Lỗi ghi DB KHÔNG được làm sập rule engine. Subject.notify() đã cô lập
    lỗi từng observer nên chuyện đó không xảy ra.
    """

    def __init__(
        self,
        logging_service: Any,
        min_interval: float = SENSOR_PERSIST_INTERVAL,
        source: str = "hardware",
    ) -> None:
        self.logging_service = logging_service
        self.min_interval = min_interval
        self.source = source
        self.written_count = 0
        self.skipped_count = 0
        self._last_write: float | None = None

    def update(self, sensor_data: SensorData) -> None:
        now = time.monotonic()
        if self._last_write is not None:
            if now - self._last_write < self.min_interval:
                self.skipped_count += 1
                return

        self._last_write = now
        self.written_count += 1

        # read_sensors() không kèm source -> mọi dòng sẽ là "unknown".
        payload = dict(sensor_data)
        payload["source"] = self.source
        self.logging_service.update(payload)


__all__ = [
    "Observer",
    "Subject",
    "RuleObserver",
    "ScheduleObserver",
    "SensorLoggingObserver",
    "SensorPersistObserver",
    "SENSOR_PERSIST_INTERVAL",
]