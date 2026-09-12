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

import argparse
import logging
import threading
import time
from typing import Any, Callable

# Cấu hình logging TRƯỚC mọi import nội bộ.
#
# stt_module.py gọi logging.basicConfig(level=INFO) ngay lúc import. Vì
# basicConfig là no-op khi root logger đã có handler, ai gọi trước thì người
# đó quyết định mức log - và trước đây người gọi trước lại là STT. Hệ quả:
# log của console có hiện hay không phụ thuộc vào việc chạy STT thật hay mock.
#
# Đặt ở đây thì entry point nắm quyền. Dòng setLevel cho paho là bắt buộc:
# thiếu nó, mỗi gói MQTT đều in một dòng và console ngập tới mức không còn
# nhìn thấy dòng ERROR nào.
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("paho").setLevel(logging.WARNING)

from config import settings

from modules.hardware_gateway.hardware_module import HardwareModule 
from modules.llm_integration.llm_strategy import (  
    GeminiLLMStrategy, MockLLMStrategy,
)

from services.command_service import CommandService
from services.logging_service import (  
    LoggingService, log_error, log_face, update_command_result,
)
from system_core.observers import (
    RuleObserver, ScheduleObserver, SensorLoggingObserver, SensorPersistObserver,
)
from services.rule_service import RuleService  


SENSOR_POLL_INTERVAL = 2.0
CONSOLE_SESSION_ID = "console"


class MainOrchestrator:
    """Cổng vào duy nhất của hệ thống."""

    def __init__(
        self,
        *,
        use_mock_stt: bool | None = None,
        use_mock_face: bool | None = None,
        hardware_mode: str | None = None,
    ) -> None:
        print("[System] Initializing YoloHome-AIoT Gateway...")

        self.use_mock_llm = settings.USE_MOCK_LLM
        self.gemini_model = settings.GEMINI_MODEL
        self.face_threshold = settings.FACE_AUTH_THRESHOLD

        # Cờ CLI (nếu có) thắng .env, để đổi chế độ mà không phải sửa file.
        self.use_mock_stt = (
            settings.USE_MOCK_STT if use_mock_stt is None else use_mock_stt
        )
        self.use_mock_face = (
            settings.USE_MOCK_FACE if use_mock_face is None else use_mock_face
        )
        self.hardware_mode = hardware_mode or settings.HARDWARE_MODE

        print(f"[Config] USE_MOCK_LLM = {self.use_mock_llm}")
        print(f"[Config] FACE_AUTH_THRESHOLD = {self.face_threshold}")
        print(f"[Config] HARDWARE_MODE = {self.hardware_mode}")

        # --- MODULES ---
        # serial_port rỗng = chế độ mô phỏng (không đụng MQTT). Giá trị chuỗi
        # HardwareModule nói chuyện với Yolo:Bit qua Adafruit IO MQTT.
        # "simulation" giữ trạng thái trong RAM và không chạm mạng.
        self.hardware_module = HardwareModule(mode=self.hardware_mode)

        self.camera: Any = None         # VideoCapture đã mở sẵn (tùy chọn)

        # Hàm lấy khung hình cho Face Auth. Đặt ở đây thay vì gọi thẳng
        # face_module.capture_frame() để test có thể thay bằng camera giả.
        self.frame_capturer: Callable[[], Any] | None = None

        self.stt_engine: Any = self._build_stt()     # Contract A
        self.face_module: Any = self._build_face()   # Contract B
        self.auth_service: Any = None                # gán sau khi có services

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

        # AuthService là điểm thực thi chính sách Face Auth (Contract B).
        # CHỈ dựng khi có recognizer thật: thiếu nó thì _handle_auth_required
        # rơi vào nhánh fail-closed và từ chối, thay vì mở cửa cho người lạ.
        #
        # Import lazy vì auth_service kéo theo face_module -> cv2 + dlib.
        # Để ở cấp module thì máy chưa cài 2 thư viện đó sẽ không import nổi
        # main.py, và toàn bộ test suite gãy ngay từ bước collect.
        # KHÔNG dựng AuthService ở đây - xem property auth_service bên dưới.
        # Dựng sẵn một lần sẽ ĐÓNG BĂNG tham chiếu tới face_module và
        # command_service tại thời điểm khởi tạo.
        self._auth_service_override: Any = None

        # --- OBSERVER PATTERN ---
        # BẮT BUỘC. Thiếu dòng này thì automation rule hỏng ÂM THẦM:
        # không lỗi, không log, test vẫn xanh, rule chỉ đơn giản không bao
        # giờ kích hoạt dù đã nằm trong database.
        self.hardware_module.attach(
            RuleObserver(self.rule_service, self.command_service)
        )

        # Lệnh hẹn giờ dùng CHUNG nhịp đập với automation rule. Vòng poll đã
        # chạy mỗi 2 giây rồi; dựng thêm một luồng hẹn giờ riêng là thêm một
        # nơi có thể chết âm thầm và thêm một thứ phải nhớ dọn khi thoát.
        self.hardware_module.attach(ScheduleObserver(self.command_service))

        # Giữ lịch sử cảm biến trong RAM cho dashboard và debug.
        self.sensor_history = SensorLoggingObserver(max_history=100)
        self.hardware_module.attach(self.sensor_history)

        # Ghi sensor_log xuống SQLite. Thiếu dòng này thì bảng sensor_log
        # rỗng vĩnh viễn và dashboard không có biểu đồ lịch sử.
        self.hardware_module.attach(SensorPersistObserver(LoggingService()))

        # KHÔNG có observer nào đẩy cảm biến LÊN Adafruit IO. Yolo:Bit đã
        # publish 4 feed đó mỗi 10 giây (24/30 data point mỗi phút của gói
        # free); backend đẩy thêm sẽ vượt hạn mức và khóa cả tài khoản.
        # Cần bơm dữ liệu giả để demo chay -> python -m tools.seed_adafruit

        self.latest_sensor_data: dict[str, Any] = {}
        self._stop_event = threading.Event()

        print("[System] Initialization complete!")

    # =========================================================================
    # Lắp ráp module ngoài (Contract A và B)
    # =========================================================================

    # =========================================================================
    # AuthService - dẫn xuất, không lưu sẵn
    # =========================================================================

    @property
    def auth_service(self) -> Any:
        """
        Cổng xác thực khuôn mặt, gắn với face_module HIỆN TẠI.

        VÌ SAO LÀ PROPERTY CHỨ KHÔNG PHẢI THUỘC TÍNH THƯỜNG
        ---------------------------------------------------
        face_module là NGUỒN SỰ THẬT DUY NHẤT cho "hệ thống đang dùng
        recognizer nào". AuthService chỉ là lớp bọc quanh nó cộng với ngưỡng.

        Dựng sẵn AuthService trong __init__ sẽ đóng băng tham chiếu: gán
        orchestrator.face_module = X sau đó thì AuthService VẪN xác thực bằng
        recognizer cũ, âm thầm và không có gì báo. Dựng theo yêu cầu thì hai
        thứ không thể lệch nhau.

        Chi phí gần bằng 0: AuthService không mở kết nối, không nạp gì, chỉ giữ
        vài tham chiếu.

        Gán tường minh (orchestrator.auth_service = X) vẫn hoạt động và được ưu
        tiên tuyệt đối - dùng cho test và cho việc thay bằng cài đặt khác.
        """
        if self._auth_service_override is not None:
            return self._auth_service_override

        if self.face_module is None:
            return None

        try:
            from services.auth_service import AuthService
        except (Exception, SystemExit) as exc:
            log_error("gateway", f"AuthService unavailable: {exc}")
            return None

        # Truyền bằng TÊN THAM SỐ, không dùng vị trí. Chữ ký thật là
        # (face_recognizer, command_service, ...); gọi theo vị trí sẽ đưa
        # command_service vào chỗ face_recognizer.
        return AuthService(
            face_recognizer=self.face_module,
            command_service=self.command_service,
            threshold=self.face_threshold,
        )

    @auth_service.setter
    def auth_service(self, value: Any) -> None:
        self._auth_service_override = value

    def _build_stt(self) -> Any:
        """
        Dựng engine STT. Trả None nếu không dùng được -> process_voice_command
        báo lỗi rõ ràng thay vì ném exception.

        Import nằm trong hàm vì stt_module kéo theo torch/transformers rất
        nặng; chế độ text không nên phải trả cái giá đó.
        """
        try:
            from modules.speech_recognition.stt_module import (
                MockSTTStrategy, PhoWhisperSTT,
            )
        except (Exception, SystemExit) as exc:
            # BẮT CẢ SystemExit: thư viện bên thứ ba có thể gọi quit()/sys.exit()
            # lúc import khi thiếu phụ thuộc. SystemExit kế thừa BaseException
            # chứ KHÔNG kế thừa Exception, nên `except Exception` để nó lọt qua
            # và cả gateway thoát giữa lúc boot. Xem chú thích ở _build_face().
            print(f"[STT] Cannot import stt_module: {exc}")
            log_error("stt", f"import failed: {exc}")
            return None

        if self.use_mock_stt:
            print("[STT] MOCK mode - canned transcript, no model download.")
            return MockSTTStrategy()

        if settings.STT_ENGINE == "faster-whisper":
            # Import lazy và bắt cả SystemExit vì cùng lý do như PhoWhisper:
            # ctranslate2/av là extension C++, có thể thoát tiến trình lúc import
            # khi thiếu runtime. Fail soft -> stt_engine=None -> dashboard báo
            # "chưa cấu hình nhận dạng giọng nói", thay vì gateway chết lúc boot.
            try:
                from modules.speech_recognition.stt_faster_whisper import (
                    FasterWhisperSTT,
                )
            except (Exception, SystemExit) as exc:
                print(f"[STT] Cannot import faster-whisper: {exc}")
                log_error("stt", f"faster-whisper import failed: {exc}")
                return None

            print(f"[STT] REAL mode - faster-whisper (CT2) {settings.CT2_MODEL_PATH}")
            return FasterWhisperSTT(model_path=settings.CT2_MODEL_PATH)

        print(f"[STT] REAL mode - {settings.PHOWHISPER_MODEL}")
        # Model tải lazy ở lần transcribe() đầu tiên, không phải ở đây.
        return PhoWhisperSTT(model_name=settings.PHOWHISPER_MODEL)

    def _build_face(self) -> Any:
        """
        Dựng recognizer khuôn mặt. Trả None nếu không dùng được.

        FAIL CLOSED: model hỏng/thiếu thì trả None chứ TUYỆT ĐỐI không tự
        hạ cấp sang MockFaceRecognizer - mock luôn nhận ra "member_1" nên
        sẽ mở cửa cho bất kỳ ai. Mock chỉ chạy khi được yêu cầu tường minh.
        """
        try:
            from modules.face_recognition.face_module import (
                MockFaceRecognizer, SvmFaceRecognizer, capture_frame,
            )
        except (Exception, SystemExit) as exc:
            # BẮT CẢ SystemExit là BẮT BUỘC ở đây, không phải phòng xa.
            #
            # face_recognition gọi quit() khi thiếu gói face_recognition_models:
            #     try:
            #         import face_recognition_models
            #     except Exception:
            #         print("Please install `face_recognition_models`...")
            #         quit()
            #
            # quit() ném SystemExit, vốn kế thừa BaseException chứ KHÔNG kế thừa
            # Exception. Chỉ bắt Exception thì nó lọt qua và CẢ GATEWAY THOÁT
            # giữa lúc boot - toàn bộ thiết kế fail soft bị vô hiệu bởi đúng một
            # lời gọi quit() trong thư viện bên thứ ba. Đã gặp thật khi dựng môi
            # trường; xem docs/Setup-Windows.md §5 và §9.
            #
            # KHÔNG dùng `except BaseException`: nó nuốt luôn KeyboardInterrupt
            # và Ctrl+C sẽ không dừng được chương trình.
            print(f"[FaceID] Cannot import face_module: {exc}")
            print("         (missing dlib/face_recognition, or the empty"
                  " face_recognition/ folder at repo root shadows the library)")
            log_error("face", f"import failed: {exc}")
            return None

        if self.use_mock_face:
            print("[FaceID] MOCK mode - ALWAYS authorizes. Demo only!")
            return MockFaceRecognizer()

        try:
            recognizer = SvmFaceRecognizer()
        except (Exception, SystemExit) as exc:
            # Cùng lý do: dlib có thể thoát tiến trình khi thiếu file .dat.
            print(f"[FaceID] Cannot load models/face_model.pkl: {exc}")
            print("         Face Auth will DENY every door command.")
            log_error("face", f"load model failed: {exc}")
            return None

        # Contract B: orchestrator sở hữu việc lấy khung hình. capture_frame()
        # tự mở/đóng camera mỗi lần quét nên không giữ webcam khi rảnh.
        # CAMERA_INDEX phải được truyền xuống, nếu không nó là cấu hình chết:
        # có trong settings.py mà không dòng nào đọc, người dùng đổi giá trị thì
        # không có gì thay đổi và cũng không có gì báo. Webcam nằm ở index 1 là
        # chuyện hay gặp khi máy có OBS, Zoom hoặc DroidCam.
        self.frame_capturer = lambda: capture_frame(
            timeout_seconds=settings.FACE_SCAN_TIMEOUT_SECONDS,
            require_blink=settings.FACE_REQUIRE_BLINK,
            camera_index=settings.CAMERA_INDEX,
        )

        print(f"[FaceID] REAL mode - blink={settings.FACE_REQUIRE_BLINK}, "
              f"timeout={settings.FACE_SCAN_TIMEOUT_SECONDS}s")
        return recognizer

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

        # Đóng kết nối MQTT. Không đóng thì luồng nền của paho còn sống và
        # tiến trình không thoát hẳn.
        try:
            self.hardware_module.stop()
        except Exception:
            pass

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
        Chụp một khung hình cho Face Auth.

        Contract B ghi rõ việc lấy frame thuộc về System owner, không phải
        Face owner. Trả None khi không lấy được - AuthService phải coi đó là
        trường hợp "không có khuôn mặt" và TỪ CHỐI, không phải cho qua.

        Hai nguồn khung hình, xét theo thứ tự:

        1. camera - một VideoCapture đã mở sẵn. Chỉ grab đúng 1 frame, không
           chờ ai xuất hiện. Dùng cho test và ngữ cảnh headless.
        2. frame_capturer - scanner của face module: mở cửa sổ, chờ tới khi
           thấy mặt (kèm chớp mắt nếu bật), rồi trả khung hình sạch.

        Cả hai cùng None -> None -> từ chối. Đúng nguyên tắc fail closed.
        """
        source = self.camera or self.frame_capturer
        if source is None:
            return None

        try:
            if self.camera is not None:
                if settings.FACE_REQUIRE_BLINK:
                    # Đường self.camera chỉ grab MỘT frame nên không thể phát
                    # hiện chớp mắt. Cấu hình đang YÊU CẦU liveness mà lại đi
                    # đường này nghĩa là chống giả mạo đã bị vô hiệu - và không
                    # có gì khác báo ra: Face Auth vẫn chạy, vẫn nhận diện, chỉ
                    # là một tấm ảnh in cũng qua được.
                    #
                    # Thuộc tính tên `camera` trông đúng là chỗ tự nhiên để đặt
                    # một VideoCapture vào, nên cái bẫy này rất dễ sập.
                    log_error(
                        "gateway",
                        "self.camera được dùng khi FACE_REQUIRE_BLINK=true: "
                        "liveness KHÔNG chạy, ảnh in có thể qua được xác thực.",
                    )

                ok, frame = self.camera.read()
                return frame if ok else None

            # capture_frame() trả None khi hết timeout mà không thấy ai.
            return self.frame_capturer()
        except Exception as exc:
            log_error("gateway", f"capture_frame failed: {exc}")
            return None

    def handle_auth_required(self, result: dict[str, Any]) -> str:
        """
        API CÔNG KHAI cho cổng Face Auth (Contract B).

        Dashboard và mọi lớp vỏ khác gọi hàm này thay vì method có gạch dưới.
        Orchestrator sở hữu camera, nên đây là chỗ duy nhất được phép mở nó.
        """
        return self._handle_auth_required(result)
    
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
# Console
# =============================================================================

def _record_audio(seconds: float) -> bytes | None:
    """
    Ghi âm từ micro mặc định. Trả None nếu thiếu sounddevice hoặc không có
    thiết bị thu - console phải báo lỗi rõ chứ không được sập.
    """
    try:
        from modules.speech_recognition.stt_module import record_wav_bytes
    except Exception as exc:
        print(f"[Mic] Cannot import recorder: {exc}")
        return None

    try:
        return record_wav_bytes(duration=seconds)
    except Exception as exc:
        print(f"[Mic] Recording failed: {exc}")
        log_error("stt", f"record failed: {exc}")
        return None


def _voice_turn(orchestrator: MainOrchestrator, seconds: float) -> None:
    """Một lượt hội thoại bằng giọng nói: ghi âm -> STT -> pipeline."""
    input(f">> Press Enter to speak ({seconds:g}s): ")

    audio = _record_audio(seconds)
    if not audio:
        return

    response = orchestrator.process_voice_command(
        audio,
        session_id=CONSOLE_SESSION_ID,
    )
    print(f"[Bot]: {response}\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m system_core.main",
        description="YoloHome-AIoT gateway console.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Voice console: record from mic each turn instead of typing.",
    )
    parser.add_argument(
        "--mock-stt",
        action="store_true",
        help="Use MockSTTStrategy (skip the PhoWhisper download).",
    )
    parser.add_argument(
        "--mock-face",
        action="store_true",
        help="Use MockFaceRecognizer - ALWAYS authorizes. Demo only.",
    )
    parser.add_argument(
        "--real-hw",
        action="store_true",
        help="Talk to Yolo:Bit over Adafruit IO MQTT (default: simulation).",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=settings.VOICE_RECORD_SECONDS,
        help="Recording length per voice turn.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)

    orchestrator = MainOrchestrator(
        # Chế độ text không cần STT -> mặc định dùng mock để khỏi tải model.
        # KHÔNG ép mock theo --voice.
        #
        # Bản trước: use_mock_stt=True if (args.mock_stt or not args.voice) else False
        # Chạy không kèm --voice là ép mock, nên USE_MOCK_STT=false trong .env
        # bị vô hiệu ÂM THẦM - không lỗi, không cảnh báo, log vẫn báo MOCK mode
        # và hai câu nói khác nhau cho ra cùng một transcript.
        #
        # --mock-stt vẫn ép mock (để test nhanh), còn lại để settings quyết định.
        # Model nạp lazy ở lần transcribe() đầu nên chế độ text không tốn gì.
        use_mock_stt=True if args.mock_stt else None,
        use_mock_face=True if args.mock_face else None,
        hardware_mode="real" if args.real_hw else None,
    )

    sensor_thread = threading.Thread(
        target=orchestrator.start_sensor_loop,
        daemon=True,
    )
    sensor_thread.start()

    time.sleep(1)

    if args.voice:
        print("\n--- VOICE CONSOLE ---")
        print("Press Enter, then speak. Example: bat den phong khach")
    else:
        print("\n--- TEXT CONSOLE ---")
        print("Type a Vietnamese command. Example: bat den phong khach")
        print("Type 'voice' to speak a single turn.")
    print("Type 'exit' to quit.\n")

    try:
        while True:
            if args.voice:
                _voice_turn(orchestrator, args.seconds)
                continue

            try:
                user_input = input("Command: ")
            except EOFError:
                break

            command = user_input.strip().lower()
            if command in {"exit", "quit"}:
                break
            if not user_input.strip():
                continue

            if command == "voice":
                _voice_turn(orchestrator, args.seconds)
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