from __future__ import annotations

import time
from collections import deque
from typing import Any

from modules.llm_integration.llm_module import (
    detect_confirmation,
    device_display_name,
    failure_response,
    load_device_registry,
    missing_slot_question,
    room_display_name,
    state_display_name,
)
from modules.llm_integration.llm_strategy import GeminiLLMStrategy
from services.logging_service import log_command, log_error, update_command_result
from services.rule_service import RuleService
from config.settings import COMMAND_HISTORY_SIZE
from services.session_service import SessionService
from system_core.contracts import (
    check_status_result,
    verify_hardware_receiver,
    verify_llm_strategy,
)
from system_core.observers import Subject
from system_core.commands import (
    Command,
    HardwareReceiver,
    create_device_command,
)
from system_core.strategies import LLMStrategy


# Trạng thái mà mỗi action đưa thiết bị về.
# Dùng để mock phần cứng có "trí nhớ", nhờ đó get_status trả lời được thật.
ACTION_TO_STATE: dict[str, str] = {
    "turn_on": "on",
    "turn_off": "off",
    "open": "open",
    "close": "closed",
}

# Trạng thái mặc định khi thiết bị chưa từng được điều khiển.
DEFAULT_STATE_BY_DEVICE: dict[str, str] = {
    "door": "closed",
}
DEFAULT_STATE = "off"


class MockHardwareModule(Subject):
    """
    Temporary hardware receiver for MVP/testing.

    Là Subject nên có thể notify() dữ liệu cảm biến cho các Observer
    (RuleObserver, dashboard...) mà không cần Yolo:Bit thật.

    Có nhớ trạng thái thiết bị trong bộ nhớ, nên get_status trả về trạng thái
    thật thay vì luôn báo "success" rỗng nghĩa.

    HỢP ĐỒNG với phần cứng thật (modules/hardware_gateway/hardware_module.py):
    - execute_command(action=get_status) PHẢI trả về {"status": ..., "state": ...}
    - state là một trong: "on" | "off" | "open" | "closed"
    """

    def __init__(self) -> None:
        super().__init__()
        self._states: dict[tuple[str, str], str] = {}
        self.sensor_data: dict[str, Any] = {
            "temperature": 28.5,
            "humidity": 70.0,
            "light": 512,
            "motion": 0,
        }

    def read_sensors(self) -> dict[str, Any]:
        return dict(self.sensor_data)

    def poll_sensors(self) -> dict[str, Any]:
        """Đọc cảm biến một lần rồi thông báo cho mọi observer."""
        sensor_data = self.read_sensors()
        self.notify(sensor_data)

        return sensor_data

    def _default_state(self, device: str) -> str:
        return DEFAULT_STATE_BY_DEVICE.get(device, DEFAULT_STATE)

    def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
        print(f"[MockHardware] execute_command: {command}")

        device = str(command.get("device", ""))
        room = str(command.get("room", ""))
        action = str(command.get("action", ""))
        key = (room, device)

        if action == "get_status":
            state = self._states.get(key, self._default_state(device))
            return {
                "status": "success",
                "state": state,
                "command": command,
            }

        new_state = ACTION_TO_STATE.get(action)
        if new_state is not None:
            self._states[key] = new_state

        return {
            "status": "success",
            "state": self._states.get(key, self._default_state(device)),
            "command": command,
        }


class CommandService:
    """
    Core coordination service.

    Responsibilities:
    - Receive user transcript.
    - Call LLM parsing + validation through LLMStrategy.
    - Route command by next_step.
    - Create concrete Command objects for hardware execution.
    - Create automation rules through RuleService.
    - Mark face-auth commands as waiting_auth.
    - Log command lifecycle to database.

    Strategy Pattern roles:
    - LLMStrategy = strategy interface
    - GeminiLLMStrategy / MockLLMStrategy = concrete strategies
    - CommandService = context

    Command Pattern roles:
    - CommandService = invoker
    - Concrete Command objects = TurnOnLightCommand, TurnOffFanCommand, etc.
    - HardwareModule / MockHardwareModule = receiver
    """

    def __init__(
        self,
        rule_service: RuleService | None = None,
        hardware_module: HardwareReceiver | None = None,
        llm_strategy: LLMStrategy | None = None,
        session_service: SessionService | None = None,
        use_mock: bool | None = None,
    ) -> None:
        self.rule_service = rule_service or RuleService()
        self.hardware_module = hardware_module or MockHardwareModule()

        # Multi-turn: nhớ câu hỏi làm rõ đang chờ trả lời.
        self.session_service = session_service or SessionService()

        # Hợp đồng phải được kiểm tra NGAY LÚC KHỞI ĐỘNG.
        # Nếu team hardware viết lại module, hoặc ai đó thêm LLM strategy mới
        # mà quên pending_command, ta muốn biết ngay - không phải giữa demo.
        verify_hardware_receiver(self.hardware_module)

        # Strategy Pattern:
        # CommandService depends on LLMStrategy, not directly on llm_module.py.
        self.llm_strategy = llm_strategy or GeminiLLMStrategy(use_mock=use_mock)
        verify_llm_strategy(self.llm_strategy)

        # Keep use_mock for backward compatibility with existing tests/call sites.
        self.use_mock = use_mock

        # Command Pattern:
        # Lưu các lệnh đã chạy thành công để có thể undo.
        # deque có maxlen: chạy 24/7 thì list thường sẽ phình vô hạn.
        self.command_history: deque[Command] = deque(maxlen=COMMAND_HISTORY_SIZE)

    def handle_transcript(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Handle a full command pipeline from transcript to system decision.

        session_id cho phép hội thoại nhiều lượt:
            User : bật đèn
            Bot  : Bạn muốn điều khiển đèn ở phòng nào?
            User : phòng khách          <- cùng session_id
            Bot  : Đã bật đèn phòng khách.

        Bỏ trống session_id -> mỗi câu là một lệnh độc lập (hành vi cũ).

        Returns:
            {
                "command_id": int,
                "ok": bool,
                "next_step": str,
                "response": str,
                "execution_status": str,
                "result": str,
                "command": dict | None,
                "session_id": str | None,
                "awaiting_reply": bool
            }
        """
        start_time = time.time()

        # Câu trả lời Có/Không cho một yêu cầu đăng ký được xử lý NGAY, KHÔNG
        # gọi LLM. Đây là dữ liệu có cấu trúc, không phải ngôn ngữ cần diễn
        # giải - và dữ liệu thật cho thấy đưa nó qua model thì cùng chữ "có"
        # ra ba kết quả khác nhau. Xử lý sớm còn tiết kiệm một lượt gọi API.
        confirmation_result = self._handle_registry_confirmation(
            transcript,
            session_id,
        )
        if confirmation_result is not None:
            return confirmation_result

        pending_command = self.session_service.get_pending(session_id)

        llm_result = self.llm_strategy.parse_and_validate(
            transcript=transcript,
            sensor_data=sensor_data,
            pending_command=pending_command,
        )

        # Tách 2 con số: latency của riêng LLM và của cả pipeline.
        # llm_latency_ms là metric để so sánh model (P3 benchmark);
        # total_latency_ms là thứ người dùng thực sự cảm nhận.
        total_latency_ms = int((time.time() - start_time) * 1000)
        llm_latency_ms = int(llm_result.get("latency_ms") or 0)

        command = llm_result.get("command") or {}
        next_step = llm_result.get("next_step", "stop")

        # Policy ghi đè quyết định của LLM -> ghi error_log để audit.
        # Đây có thể là dấu hiệu prompt injection hoặc model lỗi.
        for override in llm_result.get("policy_overrides") or []:
            log_error(
                "security_policy",
                f"Policy override on transcript={transcript!r}: {override}",
            )

        validation_status = "passed" if llm_result.get("ok") else "failed"
        execution_status = "pending"
        result_status = "pending"
        error_message = llm_result.get("error")
        response_text = command.get("response") or "Đang xử lý yêu cầu của bạn."

        command_id = self._create_initial_log(
            transcript=transcript,
            command=command,
            result_status=result_status,
            validation_status=validation_status,
            execution_status=execution_status,
            latency_ms=total_latency_ms,
            error_message=error_message,
        )

        if next_step == "execute":
            success, executed_command = self._run_command(command)

            if success:
                result_status = "success"
                execution_status = "success"

                # get_status phải trả về TRẠNG THÁI THẬT, không phải
                # "Đang kiểm tra trạng thái thiết bị." rồi im lặng.
                if command.get("action") == "get_status":
                    response_text = self._build_status_response(
                        command_data=command,
                        command=executed_command,
                    )
            else:
                result_status = "fail: hardware_error"
                execution_status = "failed"
                response_text = "Không thể gửi lệnh điều khiển tới thiết bị phần cứng."
                error_message = response_text

        elif next_step == "auth_required":
            result_status = "waiting_auth"
            execution_status = "waiting_auth"
            response_text = "Thiết bị này yêu cầu xác thực khuôn mặt để kích hoạt."

        elif next_step == "create_rule":
            rule_id = self.rule_service.create_rule(
                command_id=command_id if command_id > 0 else None,
                command=command,
            )

            if rule_id > 0:
                result_status = "success"
                execution_status = "success"
                response_text = f"Đã thiết lập quy tắc tự động hóa thành công (ID: {rule_id})."
            else:
                result_status = "fail: rule_creation_error"
                execution_status = "failed"
                response_text = "Không thể đăng ký quy tắc tự động hóa."
                error_message = response_text

        elif next_step == "clarify":
            # Phân biệt 2 loại clarify:
            # - "success"              : user nói mơ hồ -> bot hỏi lại (hành vi đúng).
            # - "clarify: missing_slot": LLM trả thiếu slot -> đây là lỗi của model,
            #                            dùng làm metric chất lượng LLM trên dashboard.
            result_status = llm_result.get("log_result") or "success"
            execution_status = "clarify"

            if self.session_service.has_reached_turn_limit(session_id):
                # Hỏi mãi mà không tiến triển -> dừng, đừng lùa user vào
                # vòng lặp clarify vô tận.
                self.session_service.clear(session_id)
                next_step = "stop"
                execution_status = "failed"
                result_status = "fail: clarify_limit"
                response_text = (
                    "Mình vẫn chưa hiểu rõ yêu cầu. "
                    "Bạn thử nói lại đầy đủ hơn nhé, ví dụ: "
                    '"bật đèn phòng khách".'
                )
                error_message = "Clarify turn limit reached."
            else:
                self.session_service.set_pending(session_id, command)

        elif next_step == "reject":
            result_status = llm_result.get("log_result") or "rejected: unknown_device"
            execution_status = "rejected"

        elif next_step == "registry_request":
            # CHƯA phải waiting_admin_review. Dòng này mới chỉ là "bot đã hỏi",
            # người dùng chưa đồng ý gì cả.
            #
            # Trước đây mọi lần nhắc tới một phòng lạ đều tạo một dòng
            # waiting_admin_review, nên hàng đợi của quản trị viên đầy những
            # yêu cầu không ai xác nhận:
            #     (1893, 'có',      'registry_request', 'waiting_admin_review')
            #     (1902, 'nhà bếp', 'registry_request', 'waiting_admin_review')
            result_status = "registry_request: awaiting_confirmation"
            execution_status = "registry_request"
            response_text = command.get("response") or (
                "Yêu cầu đăng ký phòng hoặc thiết bị mới đang chờ quản trị viên xem xét."
            )

            self.session_service.set_pending_registry(
                session_id,
                {
                    "room": command.get("room"),
                    "device": command.get("device"),
                    "action": command.get("action"),
                    "command_id": command_id,
                },
            )

        else:
            result_status = llm_result.get("log_result") or "fail: validation"
            execution_status = "failed"

            # KHÔNG được dùng lại command["response"] ở đây.
            #
            # Trường đó do LLM sinh ra TRƯỚC khi server validate, nên nó luôn
            # mang giọng thành công: "Đã bật đèn ở cửa chính." Trả lại câu đó
            # cho người dùng nghĩa là hệ thống khẳng định đã làm một việc chưa
            # hề xảy ra - trong log ghi execution_status="failed", còn người
            # dùng nghe "đã bật".
            #
            # Server soạn lại từ mã lỗi của validator, và kèm luôn những lựa
            # chọn CHẮC CHẮN TỒN TẠI trong device_registry.
            validation = llm_result.get("validation") or {}
            response_text = failure_response(command, validation)
            error_message = (
                error_message or validation.get("message") or response_text
            )

        # Vòng đời phiên hội thoại.
        # clarify -> nhánh phía trên đã set_pending, không đụng vào nữa.
        #
        # reject / registry_request KHI ĐANG CÓ pending_command là trường hợp riêng:
        # user đang TRẢ LỜI câu hỏi làm rõ nhưng đưa giá trị không hợp lệ ("phòng bếp"
        # không có trong registry). Hội thoại vẫn đang dở - xoá phiên ở đây khiến câu
        # trả lời ĐÚNG ở lượt sau bị parse cô lập:
        #
        #     bật quạt    -> clarify  (pending: fan / turn_on / room=None)
        #     phòng bếp   -> reject   (phiên bị xoá  <-- LỖI)
        #     phòng khách -> clarify  ("thiết bị nào?")  <-- đã quên "quạt"
        #
        # Giữ lại pending_command GỐC, không phải command đã merge (command chứa
        # room="phòng bếp" sai). Vẫn gọi set_pending để tăng số lượt, nhờ đó
        # SESSION_MAX_TURNS vẫn là điểm dừng và không tạo vòng lặp vô tận.
        RECOVERABLE_STEPS = {"reject", "registry_request"}

        if next_step == "clarify":
            pass
        elif next_step in RECOVERABLE_STEPS and pending_command:
            if self.session_service.has_reached_turn_limit(session_id):
                self.session_service.clear(session_id)
            else:
                self.session_service.set_pending(session_id, pending_command)
        else:
            self.session_service.clear(session_id)

        final_error = error_message if execution_status == "failed" else None

        if command_id > 0:
            self._update_final_log(
                command_id=command_id,
                result_status=result_status,
                execution_status=execution_status,
                error_message=final_error,
            )

        return {
            "command_id": command_id,
            # ok phản ánh việc hệ thống xử lý thành công, không phải việc
            # validation có passed hay không. clarify/registry_request là kết quả
            # hợp lệ, dù validation.passed=False (ví dụ code=missing_slot).
            "ok": execution_status in {
                "success",
                "waiting_auth",
                "clarify",
                "registry_request",
            },
            "next_step": next_step,
            "response": response_text,
            "execution_status": execution_status,
            "result": result_status,
            "command": command,
            "llm_latency_ms": llm_latency_ms,
            "total_latency_ms": total_latency_ms,
            "session_id": session_id,
            # True -> UI nên chờ user trả lời câu hỏi làm rõ.
            "awaiting_reply": self.session_service.get_pending(session_id) is not None,
        }

    def _handle_registry_confirmation(
        self,
        transcript: str,
        session_id: str | None,
    ) -> dict[str, Any] | None:
        """
        Xử lý câu trả lời Có/Không cho yêu cầu đăng ký đang chờ.

        Trả None nếu không áp dụng (không có yêu cầu chờ, hoặc câu này không
        phải lời đồng ý/từ chối) - khi đó pipeline chạy bình thường.

        KHÔNG gọi LLM và KHÔNG tạo dòng command_log mới: nó CẬP NHẬT đúng dòng
        đã tạo lúc bot đặt câu hỏi. Một yêu cầu, một dòng, trạng thái phản ánh
        quyết định của người dùng.
        """
        pending_registry = self.session_service.get_pending_registry(session_id)
        if not pending_registry:
            return None

        answer = detect_confirmation(transcript)
        if answer is None:
            # Người dùng trả lời bằng chuyện khác ("phòng khách") -> để pipeline
            # xử lý bình thường. Yêu cầu đăng ký bị bỏ qua, không hỏi lại.
            self.session_service.clear_registry(session_id)
            return None

        self.session_service.clear_registry(session_id)
        self.session_service.touch(session_id)

        command_id = int(pending_registry.get("command_id") or -1)
        room = pending_registry.get("room")
        device = pending_registry.get("device")

        if answer == "yes":
            result_status = "waiting_admin_review"
            room_name = room_display_name(room) if room else "phòng mới"
            response_text = (
                f"Đã ghi nhận yêu cầu thêm {room_name} vào hệ thống. "
                "Quản trị viên sẽ xem xét."
            )
        else:
            result_status = "registry_request: cancelled_by_user"
            response_text = "Đã huỷ yêu cầu đăng ký."

        if command_id > 0:
            try:
                update_command_result(
                    command_id=command_id,
                    result=result_status,
                    execution_status="registry_request",
                )
            except Exception as exc:
                log_error("gateway", f"update registry request failed: {exc}")

        # Hội thoại chưa xong: nếu vẫn còn câu lệnh dở dang thì hỏi tiếp ngay,
        # thay vì bắt người dùng nói lại từ đầu.
        pending_command = self.session_service.get_pending(session_id)
        if pending_command:
            response_text = (
                f"{response_text} "
                f"{missing_slot_question(pending_command, load_device_registry())}"
            )

        return {
            "command_id": command_id,
            "ok": True,
            "next_step": "registry_confirmed" if answer == "yes" else "registry_cancelled",
            "response": response_text,
            "execution_status": "registry_request",
            "result": result_status,
            "command": None,
            "session_id": session_id,
            "awaiting_reply": pending_command is not None,
        }

    def create_command(self, command_data: dict[str, Any]) -> Command:
        """
        Convert validated JSON command data into a concrete Command object.

        This is the mapping point of the Command Pattern.
        """
        return create_device_command(
            command_data=command_data,
            hardware=self.hardware_module,
        )

    def execute_authorized_command(self, command_data: dict[str, Any]) -> bool:
        """
        Execute a command after external authorization.

        Typical use case:
        - User says: "mở cửa chính"
        - CommandService returns waiting_auth
        - Face Auth passes
        - AuthService calls execute_authorized_command(command_data)
        """
        return self._execute_hardware_action(command_data)

    def undo_last_command(self) -> bool:
        """
        Undo the last executed command if supported.

        Sensitive operations, such as reopening a closed door, may return False.
        """
        if not self.command_history:
            return False

        last_command = self.command_history.pop()
        return last_command.undo()

    def _create_initial_log(
        self,
        transcript: str,
        command: dict[str, Any],
        result_status: str,
        validation_status: str,
        execution_status: str,
        latency_ms: int,
        error_message: str | None,
    ) -> int:
        """Create initial command_log row and return command_id."""
        try:
            return int(
                log_command(
                    transcript=transcript,
                    json_cmd=command,
                    result=result_status,
                    validation_status=validation_status,
                    execution_status=execution_status,
                    latency_ms=latency_ms,
                    error_message=error_message,
                )
            )
        except Exception as exc:
            log_error("command_service", f"Failed to write command log: {exc}")
            return -1

    def _update_final_log(
        self,
        command_id: int,
        result_status: str,
        execution_status: str,
        error_message: str | None,
    ) -> None:
        """Update final command execution state."""
        try:
            update_command_result(
                command_id=command_id,
                result=result_status,
                execution_status=execution_status,
                error_message=error_message,
            )
        except Exception as exc:
            log_error("command_service", f"Failed to update command log: {exc}")

    def _is_undoable(self, command: Command) -> bool:
        """
        Return True only for commands that have a meaningful undo action.
        """
        return getattr(command, "undo_action", None) is not None
    
    def _run_command(
        self,
        command_data: dict[str, Any],
    ) -> tuple[bool, Command | None]:
        """
        Execute hardware action through Command Pattern.

        Flow:
            JSON command data
            -> create concrete Command object
            -> command.execute()
            -> hardware receiver executes actual payload

        Trả về cả Command object để caller đọc được command.last_result,
        nhờ đó get_status không bị mất trạng thái thiết bị.
        """
        try:
            command = self.create_command(command_data)
            success = command.execute()

            # Phần cứng có tuân thủ hợp đồng get_status không?
            # Thiếu "state" -> hỏng âm thầm, nên phải log lỗi.
            check_status_result(
                hardware_name=type(self.hardware_module).__name__,
                command=command_data,
                result=command.last_result,
            )

            if success and self._is_undoable(command):
                self.command_history.append(command)

            return success, command

        except Exception as exc:
            log_error("command_service", f"Hardware command execution failed: {exc}")
            return False, None

    def _execute_hardware_action(self, command_data: dict[str, Any]) -> bool:
        """Execute hardware action and return only success/failure."""
        success, _ = self._run_command(command_data)
        return success

    def _build_status_response(
        self,
        command_data: dict[str, Any],
        command: Command | None,
    ) -> str:
        """
        Dựng câu trả lời tiếng Việt cho lệnh get_status, từ trạng thái THẬT
        mà phần cứng trả về.

        Ví dụ: "Quạt ở phòng ngủ đang bật."
        """
        state = getattr(command, "state", None) if command is not None else None

        device_text = device_display_name(command_data.get("device"))
        room_text = room_display_name(command_data.get("room"))
        state_text = state_display_name(state)

        # Tránh câu ngớ ngẩn kiểu "Cửa chính ở cửa chính đang đóng."
        # (device "door" và room "main_door" cùng hiển thị là "cửa chính")
        if device_text == room_text:
            return f"{device_text.capitalize()} {state_text}."

        return f"{device_text.capitalize()} ở {room_text} {state_text}."