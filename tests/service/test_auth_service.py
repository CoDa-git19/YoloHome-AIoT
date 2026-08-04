"""
Test cho AuthService (Contract B).

CHẠY HOÀN TOÀN OFFLINE: không database, không webcam, không dlib.
LoggingService giả được tiêm qua constructor.

NGUYÊN TẮC CỦA BỘ TEST NÀY
--------------------------
Mọi test đều assert số lần gọi execute_authorized_command(). Một test chỉ
kiểm tra chuỗi trả về sẽ vẫn XANH ngay cả khi AuthService mở cửa cho người
lạ - vì câu trả lời cho người dùng và hành động lên phần cứng là hai chuyện
khác nhau. Danh sách executed_commands là bằng chứng duy nhất.
"""

from __future__ import annotations

from typing import Any

import pytest

from config.settings import FACE_AUTH_THRESHOLD
from services.auth_service import AuthService


# =============================================================================
# Test doubles
# =============================================================================

class FakeFaceRecognizer:
    """Contract B: CHỈ trả person_name + confidence, không có cờ authorized."""

    def __init__(self, person_name: str | None = "Uyen", confidence: float = 0.95):
        self.person_name = person_name
        self.confidence = confidence
        self.calls = 0
        self.frames_seen: list[Any] = []

    def recognize(self, frame: Any = None) -> dict[str, Any]:
        self.calls += 1
        self.frames_seen.append(frame)
        return {"person_name": self.person_name, "confidence": self.confidence}


class CrashingFaceRecognizer:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, frame: Any = None):
        self.calls += 1
        raise RuntimeError("model not loaded")


class FakeCommandService:
    """executed_commands là bằng chứng phần cứng có bị kích hoạt hay không."""

    def __init__(self, ok: bool = True, crash: bool = False) -> None:
        self.ok = ok
        self.crash = crash
        self.executed_commands: list[dict[str, Any]] = []

    @property
    def execute_calls(self) -> int:
        return len(self.executed_commands)

    def execute_authorized_command(self, command: dict[str, Any]) -> bool:
        self.executed_commands.append(dict(command))
        if self.crash:
            raise RuntimeError("hardware bus error")
        return self.ok


class FakeLoggingService:
    def __init__(self, crash: bool = False) -> None:
        self.crash = crash
        self.face_logs: list[dict[str, Any]] = []
        self.command_updates: list[dict[str, Any]] = []
        self.errors: list[tuple[str, str]] = []

    def log_face(self, **kwargs: Any) -> None:
        if self.crash:
            raise RuntimeError("database is locked")
        self.face_logs.append(kwargs)

    def update_command_result(self, **kwargs: Any) -> None:
        if self.crash:
            raise RuntimeError("database is locked")
        self.command_updates.append(kwargs)

    def log_error(self, tag: str, message: str) -> None:
        self.errors.append((tag, message))


DOOR_COMMAND = {
    "intent": "control_device",
    "action": "open",
    "device": "door",
    "room": "main_door",
    "face_auth": True,
    "condition": None,
    "response": "Đã mở cửa chính.",
}


def handoff(command: dict[str, Any] | None = None, command_id: int = 42) -> dict:
    """Bàn giao từ CommandService với next_step='auth_required'."""
    return {
        "command_id": command_id,
        "next_step": "auth_required",
        "command": DOOR_COMMAND if command is None else command,
        "execution_status": "waiting_auth",
        "response": "Đã mở cửa chính.",
    }


@pytest.fixture
def logs() -> FakeLoggingService:
    return FakeLoggingService()


def build(face: Any, cmd: Any, logs: Any, threshold: float | None = None) -> AuthService:
    return AuthService(
        face_recognizer=face,
        command_service=cmd,
        threshold=threshold,
        logging_service=logs,
    )


# =============================================================================
# 1. Đường thành công
# =============================================================================

def test_valid_member_opens_the_door(logs) -> None:
    face = FakeFaceRecognizer("Uyen", 0.95)
    cmd = FakeCommandService()

    outcome = build(face, cmd, logs).authorize_and_execute(handoff(), frame="frame")

    assert face.calls == 1
    assert cmd.execute_calls == 1
    assert cmd.executed_commands[0]["device"] == "door"
    assert outcome["authorized"] is True
    assert outcome["executed"] is True


def test_frame_is_passed_through_untouched(logs) -> None:
    """Contract B: orchestrator chụp frame, AuthService chỉ chuyển tiếp."""
    face = FakeFaceRecognizer()
    service = build(face, FakeCommandService(), logs)

    service.authorize_and_execute(handoff(), frame="sentinel-frame")

    assert face.frames_seen == ["sentinel-frame"]


def test_command_is_passed_through_unchanged(logs) -> None:
    """Command đã được enforce_policy() xử lý - KHÔNG được dựng lại."""
    cmd = FakeCommandService()

    build(FakeFaceRecognizer(), cmd, logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert cmd.executed_commands[0] == DOOR_COMMAND


# =============================================================================
# 2. Fail closed - mọi ngả đều phải TỪ CHỐI
# =============================================================================

def test_missing_face_module_blocks_hardware(logs) -> None:
    cmd = FakeCommandService()

    outcome = build(None, cmd, logs).authorize_and_execute(handoff(), frame="frame")

    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa dù không có xác thực."
    assert outcome["authorized"] is False
    assert outcome["status"] == "no_face"


def test_missing_frame_blocks_hardware(logs) -> None:
    face = FakeFaceRecognizer()
    cmd = FakeCommandService()

    outcome = build(face, cmd, logs).authorize_and_execute(handoff(), frame=None)

    assert face.calls == 0, "Không có ảnh mà vẫn gọi nhận diện."
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa khi camera lỗi."
    assert outcome["status"] == "no_face"


def test_recognizer_crash_blocks_hardware(logs) -> None:
    face = CrashingFaceRecognizer()
    cmd = FakeCommandService()

    outcome = build(face, cmd, logs).authorize_and_execute(handoff(), frame="frame")

    assert face.calls == 1
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa khi model lỗi."
    assert outcome["authorized"] is False


def test_broken_handoff_blocks_hardware(logs) -> None:
    """Bàn giao thiếu device/action -> không đoán, không chạy."""
    cmd = FakeCommandService()
    broken = handoff(command={"intent": "control_device", "face_auth": True})

    outcome = build(FakeFaceRecognizer(), cmd, logs).authorize_and_execute(
        broken, frame="frame"
    )

    assert cmd.execute_calls == 0
    assert outcome["authorized"] is False


# =============================================================================
# 3. Ngưỡng là quyết định của SERVER
# =============================================================================

def test_confidence_below_threshold_is_denied(logs) -> None:
    face = FakeFaceRecognizer("Uyen", FACE_AUTH_THRESHOLD - 0.01)
    cmd = FakeCommandService()

    outcome = build(face, cmd, logs).authorize_and_execute(handoff(), frame="frame")

    assert cmd.execute_calls == 0, (
        "NGHIÊM TRỌNG: mở cửa dù confidence dưới ngưỡng."
    )
    assert outcome["status"] == "denied"


def test_confidence_exactly_at_threshold_is_allowed(logs) -> None:
    """Hợp đồng ghi >=, không phải >. Khoá lại để không ai đổi thầm."""
    face = FakeFaceRecognizer("Uyen", FACE_AUTH_THRESHOLD)
    cmd = FakeCommandService()

    build(face, cmd, logs).authorize_and_execute(handoff(), frame="frame")

    assert cmd.execute_calls == 1


def test_advisory_authorized_flag_is_ignored(logs) -> None:
    """
    Face module trả thêm authorized=True nhưng confidence thấp.

    Cờ đó chỉ là THAM KHẢO. Nếu AuthService tin nó, một model bị thay thế
    có thể tự cấp quyền cho chính mình.
    """

    class OverconfidentRecognizer:
        calls = 0

        def recognize(self, frame=None):
            OverconfidentRecognizer.calls += 1
            return {"person_name": "Uyen", "confidence": 0.10, "authorized": True}

    cmd = FakeCommandService()

    build(OverconfidentRecognizer(), cmd, logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: tin cờ authorized của module."


# =============================================================================
# 4. Nhãn "người lạ" - phòng thủ nhiều lớp
# =============================================================================

@pytest.mark.parametrize("label", ["Unknown", "unknown", "  UNKNOWN  ", "stranger"])
def test_unknown_label_is_denied_even_at_high_confidence(logs, label: str) -> None:
    """
    Lớp phòng thủ thứ hai.

    face_module đã chuyển "Unknown" thành None, nhưng AuthService không được
    phụ thuộc vào thiện chí của module phía trước: nó có thể bị thay bằng
    implementation khác quên mất luật này.
    """
    face = FakeFaceRecognizer(label, 0.99)
    cmd = FakeCommandService()

    outcome = build(face, cmd, logs).authorize_and_execute(handoff(), frame="frame")

    assert cmd.execute_calls == 0, f"NGHIÊM TRỌNG: nhãn {label!r} mở được cửa."
    assert outcome["person_name"] is None


def test_empty_person_name_is_denied(logs) -> None:
    cmd = FakeCommandService()

    build(FakeFaceRecognizer("   ", 0.99), cmd, logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert cmd.execute_calls == 0


# =============================================================================
# 5. Phần cứng lỗi SAU khi xác thực
# =============================================================================

def test_hardware_failure_is_reported_not_hidden(logs) -> None:
    face = FakeFaceRecognizer()
    cmd = FakeCommandService(ok=False)

    outcome = build(face, cmd, logs).authorize_and_execute(handoff(), frame="frame")

    assert outcome["authorized"] is True
    assert outcome["executed"] is False
    assert "không phản hồi" in outcome["response"].lower()


def test_hardware_crash_does_not_propagate(logs) -> None:
    """Phần cứng ném exception -> báo lỗi, KHÔNG làm sập gateway."""
    cmd = FakeCommandService(crash=True)

    outcome = build(FakeFaceRecognizer(), cmd, logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert outcome["executed"] is False
    assert outcome["authorized"] is True


# =============================================================================
# 6. Đóng log (Contract B - Step 3)
# =============================================================================

def test_successful_auth_closes_both_logs(logs) -> None:
    build(FakeFaceRecognizer(), FakeCommandService(), logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert len(logs.command_updates) == 1
    assert logs.command_updates[0]["execution_status"] == "success"

    assert len(logs.face_logs) == 1
    assert logs.face_logs[0]["status"] == "authorized"
    assert logs.face_logs[0]["command_id"] == 42


def test_denial_also_closes_the_log(logs) -> None:
    """
    Từ chối mà không đóng log thì dòng command_log kẹt ở waiting_auth vĩnh
    viễn, và dashboard đếm sai số lệnh đang chờ.
    """
    face = FakeFaceRecognizer("Uyen", 0.10)

    build(face, FakeCommandService(), logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert logs.command_updates[0]["execution_status"] == "rejected"
    assert logs.face_logs[0]["status"] == "denied"


def test_denied_stranger_still_gets_an_audit_row(logs) -> None:
    """person_name là NOT NULL trong schema; người lạ vẫn phải có dòng audit."""
    build(FakeFaceRecognizer(None, 0.0), FakeCommandService(), logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert logs.face_logs[0]["person_name"] == "unknown"


def test_confidence_is_clamped_for_the_database(logs) -> None:
    """
    log_face() từ chối ghi nếu confidence ngoài [0.0, 1.0]. Một giá trị lệch
    sẽ làm MẤT dòng audit của chính lần đó.
    """
    build(FakeFaceRecognizer("Uyen", 1.5), FakeCommandService(), logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert 0.0 <= logs.face_logs[0]["confidence"] <= 1.0


def test_logging_failure_does_not_change_the_security_decision(logs) -> None:
    """
    Database khoá -> vẫn phải mở cửa cho người đã xác thực đúng.

    Chiều ngược lại quan trọng hơn: một lỗi ghi log không được biến thành
    lỗi từ chối, và cũng không được biến thành lỗi cho qua.
    """
    broken_logs = FakeLoggingService(crash=True)
    cmd = FakeCommandService()

    outcome = build(FakeFaceRecognizer(), cmd, broken_logs).authorize_and_execute(
        handoff(), frame="frame"
    )

    assert cmd.execute_calls == 1
    assert outcome["authorized"] is True


def test_missing_command_id_skips_logging_without_crashing(logs) -> None:
    cmd = FakeCommandService()

    outcome = build(FakeFaceRecognizer(), cmd, logs).authorize_and_execute(
        handoff(command_id=-1), frame="frame"
    )

    assert outcome["authorized"] is True
    assert logs.face_logs == []


# =============================================================================
# 7. Ngưỡng mặc định lấy từ settings
# =============================================================================

def test_default_threshold_comes_from_settings(logs) -> None:
    service = AuthService(
        face_recognizer=FakeFaceRecognizer(),
        command_service=FakeCommandService(),
        logging_service=logs,
    )

    assert service.threshold == FACE_AUTH_THRESHOLD
    