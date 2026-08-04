"""
Test cho MainOrchestrator (system_core/main.py).

NGUYÊN TẮC
----------
1. Chạy HOÀN TOÀN OFFLINE: không gọi API Gemini, không ghi DB thật,
   không cần webcam.

2. Kiểm tra HÀNH VI, không chỉ chuỗi trả về. Một test chỉ assert nội dung
   câu trả lời sẽ vẫn xanh ngay cả khi orchestrator bỏ qua face auth và mở
   cửa cho người lạ. Vì vậy mọi test đều assert số lần gọi
   execute_authorized_command().

3. Orchestrator PHẢI ủy quyền cho CommandService.handle_transcript().
   Nhiều test dưới đây tồn tại để khóa điều đó lại - nếu ai đó dựng lại
   pipeline bằng tay trong main.py, test sẽ đỏ.
"""

from __future__ import annotations

from typing import Any

import pytest

import config.settings as settings
import database.init_db as init_db_module
import services.logging_service as logging_service_module
import services.rule_service as rule_service_module
from system_core.main import MainOrchestrator


# =============================================================================
# Mock objects
# =============================================================================

class FakeSTTEngine:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.calls = 0

    def transcribe(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        return self.transcript


class FakeCamera:
    """
    Camera giả, trả về một frame khác None.

    Cần thiết vì orchestrator chặn frame=None (fail closed). Không có camera
    giả thì mọi test face auth đều rơi vào nhánh từ chối.
    """

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0

    def read(self):
        self.calls += 1
        return self.ok, ("fake-frame" if self.ok else None)


class FakeFaceModule:
    """
    Giả lập FaceRecognizer theo Contract B.

    CHỈ trả person_name + confidence. KHÔNG trả cờ authorized - quyết định
    ngưỡng là thẩm quyền của server (FACE_AUTH_THRESHOLD), không phải của
    face module.
    """

    def __init__(
        self,
        authorized: bool,
        person_name: str | None = "Admin",
        confidence: float = 0.95,
    ) -> None:
        # authorized giữ lại cho tiện đọc test, nhưng KHÔNG trả về cho
        # orchestrator. Nó chỉ quyết định confidence cao hay thấp.
        self.person_name = person_name if authorized else None
        self.confidence = confidence if authorized else 0.3
        self.calls = 0
        self.frames_seen: list[Any] = []

    def recognize(self, frame: Any = None) -> dict[str, Any]:
        self.calls += 1
        self.frames_seen.append(frame)
        return {
            "person_name": self.person_name,
            "confidence": self.confidence,
        }


class FakeCommandService:
    """
    Giả lập CommandService.

    executed_commands là BẰNG CHỨNG DUY NHẤT cho việc phần cứng có bị kích
    hoạt hay không. Mọi test an ninh dựa vào danh sách này.
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.transcripts: list[dict[str, Any]] = []
        self.executed_commands: list[dict[str, Any]] = []
        self.execute_calls = 0
        self.hardware_ok = True

    def handle_transcript(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.transcripts.append(
            {
                "transcript": transcript,
                "sensor_data": sensor_data,
                "session_id": session_id,
            }
        )
        return self.result

    def execute_authorized_command(
        self,
        command_data: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        self.execute_calls += 1
        self.executed_commands.append(dict(command_data))
        return self.hardware_ok


# =============================================================================
# Helpers
# =============================================================================

def make_command(
    *,
    intent: str = "control_device",
    action: str | None = "turn_on",
    device: str | None = "light",
    room: str | None = "living_room",
    face_auth: bool = False,
    condition: dict[str, Any] | None = None,
    response: str = "OK",
) -> dict[str, Any]:
    """Command dict đúng 7 field theo config/command_schema.json."""
    return {
        "intent": intent,
        "action": action,
        "device": device,
        "room": room,
        "face_auth": face_auth,
        "condition": condition,
        "response": response,
    }


def make_result(
    *,
    next_step: str,
    response: str,
    command: dict[str, Any] | None = None,
    execution_status: str = "success",
    command_id: int = 0,   # 0 -> orchestrator bỏ qua ghi log, test nhanh và sạch
    ok: bool = True,
) -> dict[str, Any]:
    """Mô phỏng giá trị trả về của CommandService.handle_transcript()."""
    return {
        "command_id": command_id,
        "ok": ok,
        "next_step": next_step,
        "response": response,
        "execution_status": execution_status,
        "result": execution_status,
        "command": command if command is not None else make_command(),
        "session_id": None,
        "awaiting_reply": next_step == "clarify",
    }


def auth_required_service() -> FakeCommandService:
    """CommandService giả trả về nhánh cần xác thực khuôn mặt."""
    return FakeCommandService(
        make_result(
            next_step="auth_required",
            response="Đã mở cửa chính.",
            command=make_command(
                action="open",
                device="door",
                room="main_door",
                face_auth=True,
                response="Đã mở cửa chính.",
            ),
            execution_status="waiting_auth",
        )
    )


# =============================================================================
# Fixture
# =============================================================================

@pytest.fixture
def orchestrator(tmp_path, monkeypatch):
    """
    Khởi tạo MainOrchestrator ở chế độ hoàn toàn offline.

    Hai thứ BẮT BUỘC patch TRƯỚC khi gọi MainOrchestrator():

    1. settings.USE_MOCK_LLM - _env.example đang để false. Không patch thì
       __init__ tạo GeminiLLMStrategy(use_mock=False) và mỗi lần chạy pytest
       là một lần tốn quota API.

    2. DB_PATH ở cả 3 module - __init__ tạo RuleService(), mà
       RuleService._ensure_schema() gọi init_db() ghi thẳng vào
       database/yolohome.db thật. Mỗi module import DB_PATH riêng nên phải
       patch từng chỗ.

    Camera giả được gắn sẵn để các test face auth đi vào đường "bình thường".
    Test nào muốn kiểm tra tình huống mất camera thì tự gán camera = None.
    """
    monkeypatch.setattr(settings, "USE_MOCK_LLM", True)

    test_db = tmp_path / "test_yolohome.db"
    monkeypatch.setattr(init_db_module, "DB_PATH", test_db)
    monkeypatch.setattr(logging_service_module, "DB_PATH", test_db)
    monkeypatch.setattr(rule_service_module, "DB_PATH", test_db)
    # 3. Face/STT module thật - __init__ gọi _build_face() nạp
    #    models/face_model.pkl (~vài giây unpickle) và _build_stt() import
    #    torch. Máy chưa cài dlib hoặc chưa có file model sẽ đi một đường
    #    KHÁC HẲN, nên test trở nên phụ thuộc vào máy đang chạy.
    #    Mọi test dưới đây tự gán face_module giả nên hai module thật này
    #    không được dùng tới lần nào.
    monkeypatch.setattr(MainOrchestrator, "_build_face", lambda self: None)
    monkeypatch.setattr(MainOrchestrator, "_build_stt", lambda self: None)

    orch = MainOrchestrator()
    orch.latest_sensor_data = {"temperature": 25.0}
    orch.camera = FakeCamera()

    yield orch

    orch.stop()


# =============================================================================
# 1. Ủy quyền cho CommandService
# =============================================================================

def test_orchestrator_delegates_to_command_service(orchestrator):
    """
    Orchestrator PHẢI gọi handle_transcript(), không được tự dựng lại
    pipeline bằng parse_and_validate() + execute_authorized_command().

    Đi đường vòng sẽ mất command_log, multi-turn, query_status trạng thái
    thật và audit policy_overrides.
    """
    cmd = FakeCommandService(
        make_result(next_step="execute", response="Đã bật đèn phòng khách.")
    )
    orchestrator.command_service = cmd

    response = orchestrator.process_text_command("bật đèn phòng khách")

    assert len(cmd.transcripts) == 1, "Không hề gọi handle_transcript()."
    assert cmd.transcripts[0]["transcript"] == "bật đèn phòng khách"
    assert response == "Đã bật đèn phòng khách."


def test_sensor_context_is_passed_to_pipeline(orchestrator):
    """Dữ liệu cảm biến phải tới được LLM, nếu không thì mất ngữ cảnh."""
    cmd = FakeCommandService(make_result(next_step="execute", response="OK"))
    orchestrator.command_service = cmd
    orchestrator.latest_sensor_data = {"temperature": 31.5, "humidity": 80}

    orchestrator.process_text_command("nhiệt độ bao nhiêu")

    assert cmd.transcripts[0]["sensor_data"] == {"temperature": 31.5, "humidity": 80}


def test_session_id_is_forwarded_for_multi_turn(orchestrator):
    """
    session_id phải được truyền xuống, nếu không multi-turn slot filling
    chết âm thầm: "bật đèn" -> "phòng nào?" -> "phòng khách" thất bại.
    """
    cmd = FakeCommandService(
        make_result(next_step="clarify", response="Bạn muốn bật đèn ở phòng nào?")
    )
    orchestrator.command_service = cmd

    orchestrator.process_text_command("bật đèn", session_id="phien-1")

    assert cmd.transcripts[0]["session_id"] == "phien-1"


# =============================================================================
# 2. Lệnh thường - không được chạm Face Auth, không được chạy 2 lần
# =============================================================================

def test_normal_command_never_touches_face_auth(orchestrator):
    """
    "bật đèn phòng khách" -> KHÔNG bật camera.

    Ngoài ra handle_transcript() đã tự chạy phần cứng, nên orchestrator
    KHÔNG được gọi execute_authorized_command() lần nữa (đèn sẽ bật 2 lần).
    """
    cmd = FakeCommandService(
        make_result(next_step="execute", response="Đã bật đèn phòng khách.")
    )
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face

    response = orchestrator.process_text_command("bật đèn phòng khách")

    assert face.calls == 0, "Bật camera cho một lệnh không nhạy cảm."
    assert orchestrator.camera.calls == 0, "Chụp ảnh cho một lệnh vô hại."
    assert cmd.execute_calls == 0, "Thực thi 2 lần - lệnh sẽ chạy nhân đôi."
    assert "đã bật đèn" in response.lower()


# =============================================================================
# 3. Face Auth - xác thực THÀNH CÔNG
# =============================================================================

def test_face_auth_success_executes_command(orchestrator):
    """auth_required + khuôn mặt hợp lệ -> quét mặt TRƯỚC, mở cửa SAU."""
    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True, person_name="Danh")
    orchestrator.command_service = cmd
    orchestrator.face_module = face

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 1, "next_step=auth_required mà không xác thực."
    assert cmd.execute_calls == 1
    assert cmd.executed_commands[0]["device"] == "door"
    assert cmd.executed_commands[0]["action"] == "open"
    assert isinstance(response, str) and response


def test_orchestrator_supplies_the_frame(orchestrator):
    """
    Contract B: orchestrator chụp frame, face module CHỈ nhận diện.

    Nếu face module tự mở camera thì sẽ có 2 chỗ tranh nhau webcam.
    """
    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face

    orchestrator.process_text_command("mở cửa chính")

    assert orchestrator.camera.calls == 1, "Orchestrator không hề chụp ảnh."
    assert face.frames_seen == ["fake-frame"], (
        "Frame không được truyền xuống face module."
    )


def test_confidence_below_threshold_is_denied(orchestrator):
    """
    Ngưỡng là quyết định của SERVER, không phải của face module.

    Nhận ra người quen nhưng confidence thấp hơn FACE_AUTH_THRESHOLD thì
    vẫn phải từ chối.
    """
    cmd = auth_required_service()
    face = FakeFaceModule(
        authorized=True,
        person_name="Danh",
        confidence=orchestrator.face_threshold - 0.01,
    )
    orchestrator.command_service = cmd
    orchestrator.face_module = face

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 1
    assert cmd.execute_calls == 0, (
        "NGHIÊM TRỌNG: mở cửa dù confidence dưới ngưỡng."
    )
    assert "từ chối" in response.lower() or "denied" in response.lower()


def test_hardware_failure_after_auth_is_reported(orchestrator):
    """Xác thực xong nhưng phần cứng lỗi -> phải báo, không nói thành công."""
    cmd = auth_required_service()
    cmd.hardware_ok = False
    orchestrator.command_service = cmd
    orchestrator.face_module = FakeFaceModule(authorized=True)

    response = orchestrator.process_text_command("mở cửa chính")

    assert cmd.execute_calls == 1
    assert "đã mở cửa" not in response.lower(), (
        f"Báo thành công dù phần cứng lỗi: {response!r}"
    )


# =============================================================================
# 4. Face Auth - các đường FAIL CLOSED  (nhóm quan trọng nhất)
# =============================================================================

def test_face_auth_denied_blocks_hardware(orchestrator):
    """
    Khuôn mặt lạ -> TUYỆT ĐỐI không chạm phần cứng.

    Assertion dựa trên executed_commands, KHÔNG dựa trên nội dung câu trả
    lời. Nếu chỉ kiểm tra chuỗi, một orchestrator bỏ qua hoàn toàn face auth
    vẫn có thể làm test xanh.
    """
    cmd = auth_required_service()
    face = FakeFaceModule(authorized=False)
    orchestrator.command_service = cmd
    orchestrator.face_module = face

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 1
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: cửa mở dù xác thực thất bại."
    assert cmd.executed_commands == []
    assert "từ chối" in response.lower() or "denied" in response.lower()


def test_no_camera_blocks_hardware(orchestrator):
    """
    Chưa có camera -> TỪ CHỐI, và không được gọi tới face module.

    MockFaceRecognizer BỎ QUA tham số frame và luôn trả một danh tính hợp
    lệ, kể cả khi frame=None. Nếu orchestrator không tự chặn frame=None thì
    cửa sẽ mở dù không hề có camera nào cắm vào máy.
    """
    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face
    orchestrator.camera = None

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 0, "Gọi nhận diện dù không có frame."
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa dù không có camera."
    assert isinstance(response, str) and response


def test_frame_capturer_is_used_when_no_camera(orchestrator):
    """
    Không có camera mở sẵn -> dùng scanner của face module (capture_frame).

    Đây là đường chạy THẬT: capture_frame() tự mở/đóng webcam mỗi lần quét,
    nên orchestrator không giữ VideoCapture khi rảnh.
    """
    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True, person_name="Danh")
    orchestrator.command_service = cmd
    orchestrator.face_module = face
    orchestrator.camera = None

    calls = []
    orchestrator.frame_capturer = lambda: (calls.append(1), "scanned-frame")[1]

    orchestrator.process_text_command("mở cửa chính")

    assert calls == [1], "Không hề gọi scanner của face module."
    assert face.frames_seen == ["scanned-frame"]
    assert cmd.execute_calls == 1


def test_frame_capturer_timeout_blocks_hardware(orchestrator):
    """
    capture_frame() trả None khi hết timeout mà không thấy ai.

    Phải coi đó là "không có khuôn mặt" và TỪ CHỐI - không được để lọt
    frame=None xuống recognizer, vì mock sẽ vẫn trả danh tính hợp lệ.
    """
    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face
    orchestrator.camera = None
    orchestrator.frame_capturer = lambda: None

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 0, "Gọi nhận diện dù scanner không lấy được frame."
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa khi hết timeout quét."
    assert isinstance(response, str) and response


def test_frame_capturer_crash_blocks_hardware(orchestrator):
    """Webcam lỗi giữa chừng -> fail closed, không sập chương trình."""

    def exploding_capturer():
        raise RuntimeError("camera disconnected")

    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face
    orchestrator.camera = None
    orchestrator.frame_capturer = exploding_capturer

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 0
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa khi scanner lỗi."
    assert isinstance(response, str) and response


def test_camera_read_failure_blocks_hardware(orchestrator):
    """Camera có mặt nhưng đọc lỗi (che ống kính, USB lỏng) -> từ chối."""
    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face
    orchestrator.camera = FakeCamera(ok=False)

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 0
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa khi đọc camera lỗi."
    assert isinstance(response, str) and response


def test_missing_face_module_blocks_hardware(orchestrator):
    """
    Face module chưa tích hợp -> fail CLOSED.

    Nhóm chưa xong phần này. Nếu thiếu module mà cửa vẫn mở thì đó là lỗ
    hổng nghiêm trọng, không phải "tính năng tạm thời".
    """
    cmd = auth_required_service()
    orchestrator.command_service = cmd
    orchestrator.face_module = None
    orchestrator.auth_service = None

    response = orchestrator.process_text_command("mở cửa chính")

    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa dù không có xác thực."
    assert isinstance(response, str) and response


def test_face_module_crash_blocks_hardware(orchestrator):
    """Model nhận diện ném exception -> không sập hệ thống, và không mở cửa."""

    class CrashingFaceModule:
        def __init__(self) -> None:
            self.calls = 0

        def recognize(self, frame: Any = None):
            self.calls += 1
            raise RuntimeError("model not loaded")

    cmd = auth_required_service()
    face = CrashingFaceModule()
    orchestrator.command_service = cmd
    orchestrator.face_module = face

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 1
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa khi nhận diện lỗi."
    assert isinstance(response, str) and response


# =============================================================================
# 5. AuthService (phần của Ly) được ưu tiên khi đã có
# =============================================================================

def test_auth_service_takes_priority_over_face_module(orchestrator):
    """
    Khi AuthService sẵn sàng, orchestrator phải ủy quyền cho nó và KHÔNG tự
    gọi face_module hay execute_authorized_command - nếu không sẽ thực thi
    2 lần và ghi log trùng.
    """

    class FakeAuthService:
        def __init__(self) -> None:
            self.calls = 0
            self.frames_seen: list[Any] = []

        def authorize_and_execute(self, result, frame=None, *a: Any, **kw: Any):
            self.calls += 1
            self.frames_seen.append(frame)
            return {"authorized": True, "response": "Đã mở cửa chính."}

    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True)
    auth = FakeAuthService()

    orchestrator.command_service = cmd
    orchestrator.face_module = face
    orchestrator.auth_service = auth

    response = orchestrator.process_text_command("mở cửa chính")

    assert auth.calls == 1
    assert auth.frames_seen == ["fake-frame"], "Không truyền frame cho AuthService."
    assert face.calls == 0, "Gọi face_module dù đã có AuthService."
    assert cmd.execute_calls == 0, "Thực thi 2 lần."
    assert "đã mở cửa" in response.lower()


def test_auth_service_crash_blocks_hardware(orchestrator):
    """AuthService lỗi -> fail closed, không rơi ngược về đường tạm thời."""

    class CrashingAuthService:
        def authorize_and_execute(self, result, frame=None, *a: Any, **kw: Any):
            raise RuntimeError("auth backend down")

    cmd = auth_required_service()
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face
    orchestrator.auth_service = CrashingAuthService()

    response = orchestrator.process_text_command("mở cửa chính")

    assert face.calls == 0, "Rơi về fallback khi AuthService lỗi."
    assert cmd.execute_calls == 0, "NGHIÊM TRỌNG: mở cửa khi AuthService lỗi."
    assert isinstance(response, str) and response


# =============================================================================
# 6. Các next_step không cần Face Auth
# =============================================================================

@pytest.mark.parametrize(
    "next_step,response_text",
    [
        ("create_rule", "Đã thiết lập quy tắc tự động hóa thành công (ID: 1)."),
        ("clarify", "Bạn muốn bật đèn ở phòng nào?"),
        ("registry_request", "Yêu cầu đang chờ quản trị viên xem xét."),
        ("reject", "Mình không hỗ trợ thiết bị này."),
        ("stop", "Xin lỗi, mình chưa hiểu rõ yêu cầu của bạn."),
    ],
)
def test_non_auth_steps_return_response_without_extra_execution(
    orchestrator, next_step, response_text
):
    """
    handle_transcript() đã xử lý trọn vẹn các nhánh này. Orchestrator chỉ
    việc trả response, không được làm gì thêm.

    Riêng create_rule: KHÔNG được bật quạt ngay lúc tạo rule. Rule chỉ chạy
    khi RuleObserver thấy điều kiện chuyển từ sai sang đúng (edge-triggered).
    """
    cmd = FakeCommandService(
        make_result(next_step=next_step, response=response_text)
    )
    face = FakeFaceModule(authorized=True)
    orchestrator.command_service = cmd
    orchestrator.face_module = face

    response = orchestrator.process_text_command("một câu lệnh nào đó")

    assert response == response_text
    assert cmd.execute_calls == 0
    assert face.calls == 0


# =============================================================================
# 7. Đường vào giọng nói (Contract A)
# =============================================================================

def test_voice_and_text_share_the_same_pipeline(orchestrator):
    """
    process_voice_command chỉ khác process_text_command ở bước STT.
    Hai đường vào lệch nhau là nguồn bug rất khó tìm.
    """
    cmd = FakeCommandService(
        make_result(next_step="execute", response="Đã tắt quạt phòng ngủ.")
    )
    stt = FakeSTTEngine("tắt quạt phòng ngủ")
    orchestrator.command_service = cmd
    orchestrator.stt_engine = stt

    response = orchestrator.process_voice_command(b"dummy_audio")

    assert stt.calls == 1
    assert cmd.transcripts[0]["transcript"] == "tắt quạt phòng ngủ"
    assert response == "Đã tắt quạt phòng ngủ."


def test_voice_command_without_stt_does_not_crash(orchestrator):
    """STT chưa cấu hình -> báo lỗi rõ ràng, không ném exception."""
    cmd = FakeCommandService(make_result(next_step="execute", response="OK"))
    orchestrator.command_service = cmd
    orchestrator.stt_engine = None

    response = orchestrator.process_voice_command(b"dummy_audio")

    assert isinstance(response, str) and response
    assert cmd.transcripts == [], "Gọi pipeline dù chưa có transcript."


def test_empty_transcript_does_not_reach_pipeline(orchestrator):
    """STT trả chuỗi rỗng (im lặng) -> hỏi lại, không gọi LLM tốn quota."""
    cmd = FakeCommandService(make_result(next_step="execute", response="OK"))
    orchestrator.command_service = cmd
    orchestrator.stt_engine = FakeSTTEngine("   ")

    response = orchestrator.process_voice_command(b"dummy_audio")

    assert cmd.transcripts == []
    assert isinstance(response, str) and response


def test_stt_crash_does_not_break_system(orchestrator):
    """Model STT ném exception -> hệ thống vẫn sống."""

    class CrashingSTT:
        def transcribe(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("model not loaded")

    cmd = FakeCommandService(make_result(next_step="execute", response="OK"))
    orchestrator.command_service = cmd
    orchestrator.stt_engine = CrashingSTT()

    response = orchestrator.process_voice_command(b"dummy_audio")

    assert isinstance(response, str) and response
    assert cmd.transcripts == []


# =============================================================================
# 8. Observer wiring - hỏng ÂM THẦM nếu quên
# =============================================================================

def test_rule_observer_is_attached(orchestrator):
    """
    Thiếu RuleObserver thì automation rule không bao giờ chạy: không lỗi,
    không log, các test khác vẫn xanh. Đây là test duy nhất bắt được.
    """
    from system_core.observers import RuleObserver

    observers = orchestrator.hardware_module.observers

    assert any(isinstance(obs, RuleObserver) for obs in observers), (
        "RuleObserver chưa được attach - automation rule sẽ chết âm thầm."
    )