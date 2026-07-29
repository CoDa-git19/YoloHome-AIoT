"""
MainOrchestrator - System Gateway của YoloHome-AIoT.

VAI TRÒ
-------
Orchestrator chỉ làm 3 việc:

  1. Khởi tạo và lắp ráp các module (wiring).
  2. Chạy vòng đọc cảm biến (nhịp tim của automation rule).
  3. Nối 2 seam bên ngoài: STT -> pipeline, và pipeline -> Face Auth.

Orchestrator KHÔNG tự dựng lại pipeline xử lý lệnh. Toàn bộ luồng
transcript -> LLM -> validate -> execute -> ghi log đã nằm trong
CommandService.handle_transcript(). Gọi thẳng parse_and_validate() rồi
execute_authorized_command() sẽ mất:

    - command_log / latency_ms  (dashboard trống)
    - multi-turn slot filling   ("bật đèn" -> "phòng nào?")
    - query_status trạng thái thật của thiết bị
    - audit policy_overrides    (bằng chứng chống prompt injection)

Xem docs/Integration-Contracts.md để biết chi tiết 2 contract.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from config import settings

from modules.hardware_gateway.hardware_module import HardwareModule
from modules.llm_integration.llm_strategy import GeminiLLMStrategy, MockLLMStrategy
# from modules.speech_recognition.stt_module import STTModule
# from modules.face_recognition.face_module import FaceModule
# from services.auth_service import AuthService

from services.command_service import CommandService
from services.logging_service import (
    LoggingService, log_error, log_face, update_command_result,
)
from system_core.observers import (
    RuleObserver, SensorLoggingObserver, SensorPersistObserver, AdafruitPublisher
)
from services.rule_service import RuleService


SENSOR_POLL_INTERVAL = 2.0
CONSOLE_SESSION_ID = "console"


class MainOrchestrator:
    """Cổng vào duy nhất của hệ thống."""

    def __init__(self) -> None:
        print("[System] Initializing YoloHome-AIoT Gateway...")

        self.use_mock_llm = settings.USE_MOCK_LLM
        self.gemini_model = settings.GEMINI_MODEL
        self.face_threshold = settings.FACE_AUTH_THRESHOLD

        print(f"[Config] USE_MOCK_LLM = {self.use_mock_llm}")
        print(f"[Config] FACE_AUTH_THRESHOLD = {self.face_threshold}")

        # --- MODULES ---
        self.hardware_module = HardwareModule()
        self.stt_engine = None          # Contract A
        self.face_module = None         # Contract B
        self.auth_service = None        # Contract B (Ly) - chưa có
        self.camera = None              # cv2.VideoCapture - orchestrator SỞ HỮU
        self.liveness_capture = None    # face_module.capture_frame, nếu bật

        # _wire_face_auth() / _wire_stt() KHÔNG gọi ở đây. AuthService cần
        # command_service, mà nó mãi khối SERVICES bên dưới mới tồn tại.
        # Xem chú thích ở chỗ gọi thật sự.

        if self.use_mock_llm:
            print("[System] Running LLM in MOCK mode (no API calls).")
            self.llm_engine = MockLLMStrategy()
        else:
            print(f"[System] Running LLM in REAL mode using {self.gemini_model}.")
            self.llm_engine = GeminiLLMStrategy(use_mock=False)

        # --- SERVICES ---
        self.rule_service = RuleService()
        self.command_service = CommandService(
            rule_service=self.rule_service,
            hardware_module=self.hardware_module,
            # Dùng chung 1 LLMStrategy, tránh khởi tạo Gemini client 2 lần.
            llm_strategy=self.llm_engine,
            use_mock=self.use_mock_llm,
        )

        # --- MODULE NGOẠI VI ---
        # PHẢI đứng sau khối SERVICES: _wire_face_auth() dựng AuthService, mà
        # AuthService nhận command_service qua constructor. Chuyển hai dòng này
        # lên trên sẽ ném AttributeError ngay lúc boot.
        self._wire_face_auth()
        self._wire_stt()

        # --- OBSERVER PATTERN ---
        # BẮT BUỘC. Thiếu dòng này thì automation rule hỏng ÂM THẦM:
        # không lỗi, không log, test vẫn xanh, rule chỉ đơn giản không bao
        # giờ kích hoạt dù đã nằm trong database.
        self.hardware_module.attach(
            RuleObserver(self.rule_service, self.command_service)
        )

        # Giữ lịch sử cảm biến trong RAM cho dashboard và debug.
        self.sensor_history = SensorLoggingObserver(max_history=100)
        self.hardware_module.attach(self.sensor_history)

        # Ghi sensor_log xuống SQLite. Thiếu dòng này thì bảng sensor_log
        # rỗng vĩnh viễn và dashboard không có biểu đồ lịch sử.
        self.hardware_module.attach(SensorPersistObserver(LoggingService()))

        # Đẩy cảm biến lên Adafruit IO. An toàn khi chưa có credential:
        # publish_to_adafruit() tự bỏ qua nếu _get_aio_client() trả None.
        if settings.ADAFRUIT_IO_USERNAME and settings.ADAFRUIT_IO_KEY:
            self.hardware_module.attach(AdafruitPublisher(self.hardware_module))
            print("[Config] Adafruit IO publishing = ON")
        else:
            print("[Config] Adafruit IO publishing = OFF (thiếu credential)")

        self.latest_sensor_data: dict[str, Any] = {}
        self._stop_event = threading.Event()

        print("[System] Initialization complete!")

    # =========================================================================
    # Wiring các module tùy chọn
    #
    # Cả hai hàm dưới đây FAIL SOFT: thiếu thư viện, thiếu model, thiếu webcam
    # thì gateway vẫn boot, chỉ tính năng đó tắt và có log rõ ràng. Máy một bạn
    # chưa cài được dlib không được phép làm sập hệ thống của cả nhóm.
    #
    # Mặc định TẮT (ENABLE_FACE_AUTH / ENABLE_STT = false) nên test suite và CI
    # không bao giờ chạm vào webcam hay tải model.
    # =========================================================================

    def _wire_face_auth(self) -> None:
        if not settings.ENABLE_FACE_AUTH:
            print("[Config] Face Auth = OFF (ENABLE_FACE_AUTH=false)")
            return

        try:
            import cv2

            # Nạp SẴN dlib ngay lúc boot. face_module cố ý import nặng bên
            # trong hàm để test chạy offline được; nạp trước ở đây thì lần
            # nhận diện đầu tiên không bị khựng 1-2 giây giữa lúc demo, và
            # lỗi thiếu thư viện lộ ra ngay bây giờ thay vì lúc mở cửa.
            import face_recognition  # noqa: F401

            from modules.face_recognition.face_module import (
                SvmFaceRecognizer,
                capture_frame,
            )

            self.face_module = SvmFaceRecognizer()

            camera = cv2.VideoCapture(settings.CAMERA_INDEX)
            if not camera.isOpened():
                camera.release()
                raise RuntimeError(
                    f"không mở được camera index={settings.CAMERA_INDEX}"
                )

            # Contract B: orchestrator là CHỦ SỞ HỮU DUY NHẤT của VideoCapture.
            # Face module đọc frame từ đối tượng này qua tham số cap, KHÔNG tự
            # mở camera - hai chỗ cùng giữ webcam thì chỗ thứ hai nhận None và
            # Face Auth từ chối mọi lệnh mà không rõ nguyên nhân.
            self.camera = camera
            self.liveness_capture = capture_frame

            # AuthService dựng TRONG CÙNG try/except với face_module là có chủ
            # đích. Hai thứ này phải có cùng vòng đời: không bao giờ được rơi
            # vào trạng thái auth_service tồn tại mà face_module là None, hay
            # ngược lại. Tách ra hai hàm thì bất biến đó nằm ở hai chỗ và sẽ
            # lệch nhau lúc nào không biết.
            from services.auth_service import AuthService

            self.auth_service = AuthService(
                face_recognizer=self.face_module,
                command_service=self.command_service,
            )

            print("[Config] Face Auth = ON (SvmFaceRecognizer + liveness)")

        except (Exception, SystemExit) as exc:
            # BẮT CẢ SystemExit là có chủ đích.
            #
            # Thư viện face_recognition gọi quit() khi thiếu gói model:
            #     try:
            #         import face_recognition_models
            #     except Exception:
            #         print("Please install `face_recognition_models`...")
            #         quit()
            #
            # quit() ném SystemExit, mà SystemExit kế thừa BaseException chứ
            # KHÔNG kế thừa Exception. Chỉ bắt Exception thì nó lọt qua, và cả
            # gateway thoát ngay giữa lúc boot - toàn bộ thiết kế fail soft bị
            # vô hiệu bởi đúng một lời gọi quit() trong thư viện bên thứ ba.
            #
            # KHÔNG dùng `except BaseException`: nó nuốt luôn KeyboardInterrupt
            # và Ctrl+C sẽ không dừng được chương trình.
            #
            # Fail closed toàn phần. Để sót auth_service khác None ở đây nghĩa
            # là _handle_auth_required() sẽ đi nhánh AuthService với một
            # face_recognizer hỏng, thay vì nhánh fallback.
            self.face_module = None
            self.auth_service = None
            self.camera = None
            self.liveness_capture = None
            log_error("gateway", f"Face Auth wiring failed: {exc}")
            print(f"[Config] Face Auth = OFF ({exc})")

    def _wire_stt(self) -> None:
        if not settings.ENABLE_STT:
            print("[Config] STT = OFF (ENABLE_STT=false)")
            return

        try:
            from modules.speech_recognition.stt_module import PhoWhisperSTT

            self.stt_engine = PhoWhisperSTT(model_name=settings.STT_MODEL_NAME)
            print(f"[Config] STT = ON ({self.stt_engine.model_name})")

        except (Exception, SystemExit) as exc:
            # Cùng lý do với _wire_face_auth(): thư viện bên thứ ba có thể gọi
            # quit()/sys.exit() lúc import khi thiếu phụ thuộc. Thiếu STT
            # không được phép làm sập gateway.
            self.stt_engine = None
            log_error("gateway", f"STT wiring failed: {exc}")
            print(f"[Config] STT = OFF ({exc})")

    # =========================================================================
    # Vòng đọc cảm biến
    # =========================================================================

    def start_sensor_loop(self) -> None:
        """
        Nhịp tim của hệ thống.

        poll_sensors() -> notify() -> RuleObserver -> RuleService.
        Không chạy vòng này thì automation rule không bao giờ được đánh giá.
        """
        print("[System] Starting background sensor monitoring thread...")

        while not self._stop_event.is_set():
            try:
                self.latest_sensor_data = self.hardware_module.poll_sensors()
            except Exception as exc:
                log_error("gateway", f"Sensor loop error: {exc}")
                print(f"[System] Sensor loop error: {exc}")

            self._stop_event.wait(SENSOR_POLL_INTERVAL)

    def stop(self) -> None:
        """Dừng vòng cảm biến (dùng khi thoát chương trình hoặc trong test)."""
        self._stop_event.set()

        # Trả webcam lại cho hệ điều hành. Không release thì trên Windows lần
        # chạy sau sẽ báo "camera đang được ứng dụng khác sử dụng".
        if self.camera is not None:
            try:
                self.camera.release()
            except Exception:
                pass
            self.camera = None

    # =========================================================================
    # Hai đường vào: giọng nói và văn bản
    # =========================================================================

    def process_voice_command(
        self,
        audio_data: bytes,
        session_id: str | None = CONSOLE_SESSION_ID,
    ) -> str:
        """Contract A: audio -> STT -> pipeline chung."""
        print("\n" + "=" * 50)
        print("[Pipeline] Processing new voice command...")

        if not self.stt_engine:
            return "Chưa cấu hình nhận dạng giọng nói. Bạn hãy nhập bằng văn bản."

        print("[STT] Converting speech to text...")
        try:
            transcript = self.stt_engine.transcribe(audio_data)
        except Exception as exc:
            log_error("stt", f"transcribe failed: {exc}")
            return "Không nhận dạng được giọng nói. Bạn thử lại giúp mình nhé."

        if not transcript or not transcript.strip():
            return "Mình chưa nghe rõ. Bạn nói lại giúp mình nhé."

        print(f"[STT] Transcript: {transcript!r}")
        return self._process_transcript(transcript, session_id)

    def process_text_command(
        self,
        transcript: str,
        session_id: str | None = CONSOLE_SESSION_ID,
    ) -> str:
        """Đường vào bằng văn bản: bỏ bước STT, phần còn lại y hệt."""
        print("\n" + "=" * 50)
        print("[Pipeline] Processing new text command...")

        return self._process_transcript(transcript, session_id)

    # =========================================================================
    # Pipeline chung
    # =========================================================================

    def _process_transcript(
        self,
        transcript: str,
        session_id: str | None,
    ) -> str:
        """
        Ủy quyền TOÀN BỘ cho CommandService.

        handle_transcript() đã tự xử lý execute / clarify / create_rule /
        registry_request / reject và ghi command_log. Orchestrator chỉ còn
        phải xử lý đúng MỘT trường hợp: auth_required, vì Face Auth nằm
        ngoài phạm vi của CommandService.
        """
        print("[LLM] Analyzing intent with current sensor context...")

        result = self.command_service.handle_transcript(
            transcript,
            sensor_data=self.latest_sensor_data,
            session_id=session_id,
        )

        next_step = result.get("next_step", "stop")
        print(f"[Pipeline] next_step = {next_step}")

        if next_step == "auth_required":
            return self._handle_auth_required(result)

        print("=" * 50)
        return result.get("response") or "Mình chưa xử lý được yêu cầu này."

    # =========================================================================
    # Contract B - Face Auth
    # =========================================================================

    def capture_frame(self):
        """
        Lấy một khung hình cho Face Auth.

        Contract B (đã sửa đổi): VideoCapture thuộc về System owner, nhưng khi
        bật liveness thì face module cần ĐỌC NHIỀU FRAME từ chính đối tượng đó -
        chớp mắt không thể phát hiện bằng một khung hình duy nhất. Vì vậy ở đây
        truyền self.camera xuống thay vì để face module tự mở camera.

        Nhánh self.camera.read() bên dưới được giữ lại có chủ đích: khi
        liveness_capture là None (test suite, hoặc ENABLE_FACE_AUTH=false),
        hành vi y hệt bản cũ.

        Trả None khi chưa có camera / không bắt được chớp mắt. AuthService phải
        coi đó là "không có khuôn mặt" và TỪ CHỐI, không phải cho qua.
        """
        if self.camera is None:
            return None

        if self.liveness_capture is not None:
            try:
                return self.liveness_capture(
                    cap=self.camera,
                    require_blink=settings.FACE_REQUIRE_BLINK,
                )
            except Exception as exc:
                log_error("gateway", f"liveness capture failed: {exc}")
                return None

        try:
            ok, frame = self.camera.read()
            return frame if ok else None
        except Exception as exc:
            log_error("gateway", f"capture_frame failed: {exc}")
            return None

    def _handle_auth_required(self, result: dict[str, Any]) -> str:
        """
        Cổng xác thực cho các hành động nhạy cảm (hiện tại: door.open).

        NGUYÊN TẮC: FAIL CLOSED.
        Thiếu module, lỗi camera, xác thực thất bại -> KHÔNG chạm phần cứng.
        Cờ face_auth=True chỉ ĐỊNH TUYẾN sang đây, nó không tự chặn gì cả;
        execute_authorized_command() cũng không kiểm tra lại. Vì vậy hàm này
        là chốt chặn duy nhất ở thời điểm thực thi.
        """
        command = result.get("command") or {}
        command_id = int(result.get("command_id") or -1)

        print("[FaceID] Security clearance required. Activating camera...")

        # (1) Đường chính thức: AuthService (Contract B).
        #     AuthService tự lo threshold, execute và đóng log.
        if self.auth_service is not None:
            try:
                outcome = self.auth_service.authorize_and_execute(
                    result,
                    frame=self.capture_frame(),
                )
                return outcome.get("response") or result.get("response") or "Đã xử lý."
            except Exception as exc:
                log_error("gateway", f"auth_service failed: {exc}")
                return "Xác thực khuôn mặt gặp sự cố. Vì an toàn, lệnh đã bị hủy."

        # (2) AuthService chưa có -> đường TẠM THỜI dùng thẳng face_module.
        #     Xóa toàn bộ nhánh này khi services/auth_service.py hoàn tất.
        return self._fallback_auth(command, command_id, result)

    def _fallback_auth(
        self,
        command: dict[str, Any],
        command_id: int,
        result: dict[str, Any],
    ) -> str:
        """
        Đường xác thực tạm thời khi AuthService chưa sẵn sàng.

        Bám sát Contract B - Step 2: NGƯỠNG LÀ QUYẾT ĐỊNH CỦA SERVER.
        Nếu face module có trả cờ authorized thì đó chỉ là tham khảo; ở đây
        tự tính lại từ person_name + confidence so với FACE_AUTH_THRESHOLD.
        """
        if self.face_module is None:
            print("   => DENIED: face module chưa sẵn sàng.")
            self._close_auth_log(
                command_id,
                command=command,
                status="no_face",
                executed=False,
                person_name=None,
                confidence=0.0,
            )
            return "Từ chối truy cập: hệ thống xác thực khuôn mặt chưa sẵn sàng."

        frame = self.capture_frame()
        if frame is None:
            print("   => DENIED: chưa lấy được frame từ camera.")
            self._close_auth_log(
                command_id,
                command=command,
                status="no_face",
                executed=False,
                person_name=None,
                confidence=0.0,
            )
            return "Từ chối truy cập: không lấy được hình ảnh từ camera."

        try:
            # Contract B: orchestrator chụp frame, face module chỉ nhận diện.
            face_result = self.face_module.recognize(frame) or {}
        except Exception as exc:
            log_error("face", f"recognize failed: {exc}")
            self._close_auth_log(
                command_id,
                command=command,
                status="no_face",
                executed=False,
                person_name=None,
                confidence=0.0,
            )
            return "Xác thực khuôn mặt gặp sự cố. Vì an toàn, lệnh đã bị hủy."

        person_name = face_result.get("person_name")
        confidence = float(face_result.get("confidence") or 0.0)

        # Contract B: ngưỡng do server quyết định, không tin cờ của module.
        authorized = bool(person_name) and confidence >= self.face_threshold

        if not authorized:
            print(
                f"   => DENIED: person={person_name!r}, "
                f"confidence={confidence:.2f} < {self.face_threshold}"
            )
            self._close_auth_log(
                command_id,
                command=command,
                status="denied",
                executed=False,
                person_name=person_name,
                confidence=confidence,
            )
            return "Từ chối truy cập. Xác thực danh tính thất bại."

        print(f"   => Verified! Welcome, {person_name} ({confidence:.2f}).")
        print("[Execute] Running authorized command...")

        executed = bool(self.command_service.execute_authorized_command(command))

        self._close_auth_log(
            command_id,
            command=command,
            status="authorized",
            executed=executed,
            person_name=person_name,
            confidence=confidence,
        )

        print("=" * 50)
        if not executed:
            return "Xác thực thành công nhưng thiết bị không phản hồi."

        return result.get("response") or command.get("response") or "Đã thực hiện lệnh."

    def _close_auth_log(
        self,
        command_id: int,
        *,
        command: dict[str, Any],
        status: str,
        executed: bool,
        person_name: str | None,
        confidence: float,
    ) -> None:
        """
        Đóng command_log và ghi face_log.

        Bình thường đây là TRÁCH NHIỆM CỦA AuthService (Contract B - Step 3).
        Hàm này chỉ chạy ở đường tạm thời, để command_log không kẹt vĩnh viễn
        ở waiting_auth và làm hỏng số liệu dashboard.

        status hợp lệ theo schema: authorized | denied | no_face | timeout
        """
        if command_id <= 0:
            return

        authorized = status == "authorized"

        if authorized and executed:
            result_status, execution_status = "success", "success"
        elif authorized:
            result_status, execution_status = "fail: hardware_error", "failed"
        else:
            result_status, execution_status = "rejected: face_auth", "rejected"

        try:
            update_command_result(
                command_id=command_id,
                result=result_status,
                execution_status=execution_status,
                error_message=None if execution_status == "success" else result_status,
            )
        except Exception as exc:
            log_error("gateway", f"update_command_result failed: {exc}")

        try:
            log_face(
                person_name=person_name or "unknown",
                # log_face bắt buộc confidence trong [0.0, 1.0].
                confidence=min(max(confidence, 0.0), 1.0),
                status=status,
                command_id=command_id,
                triggered_by="voice_command",
                device=command.get("device"),
                room=command.get("room"),
                action_result=execution_status,
            )
        except Exception as exc:
            log_error("gateway", f"log_face failed: {exc}")


# =============================================================================
# Console tạm thời (dùng khi STT và dashboard chưa sẵn sàng)
# =============================================================================

def main() -> None:
    orchestrator = MainOrchestrator()

    sensor_thread = threading.Thread(
        target=orchestrator.start_sensor_loop,
        daemon=True,
    )
    sensor_thread.start()

    time.sleep(1)

    print("\n--- CONSOLE CHAT (tạm thời, STT chưa sẵn sàng) ---")
    print("Gõ câu lệnh tiếng Việt, ví dụ: bật đèn phòng khách")
    print("Gõ 'exit' để thoát.\n")

    try:
        while True:
            try:
                user_input = input("Nhập lệnh: ")
            except EOFError:
                break

            if user_input.strip().lower() in {"exit", "quit"}:
                break
            if not user_input.strip():
                continue

            # Dùng chung session_id -> multi-turn hoạt động:
            #   "bật đèn"      -> "Bạn muốn bật đèn ở phòng nào?"
            #   "phòng khách"  -> "Đã bật đèn phòng khách."
            response = orchestrator.process_text_command(
                user_input,
                session_id=CONSOLE_SESSION_ID,
            )
            print(f"[Bot]: {response}\n")

    except KeyboardInterrupt:
        print("\n[System] Interrupted.")
    finally:
        orchestrator.stop()
        print("[System] Shutting down.")


if __name__ == "__main__":
    main()