from __future__ import annotations

import time
from typing import Any

from modules.llm_integration.llm_strategy import GeminiLLMStrategy
from services.logging_service import log_command, log_error, update_command_result
from services.rule_service import RuleService
from system_core.commands import (
    Command,
    HardwareReceiver,
    create_device_command,
)
from system_core.strategies import LLMStrategy


class MockHardwareModule:
    """
    Temporary hardware receiver for MVP/testing.

    Later this can be replaced by:
    modules.hardware_gateway.hardware_module.HardwareModule
    """

    def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
        print(f"[MockHardware] execute_command: {command}")
        return {
            "status": "success",
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
        use_mock: bool = True,
    ) -> None:
        self.rule_service = rule_service or RuleService()
        self.hardware_module = hardware_module or MockHardwareModule()

        # Strategy Pattern:
        # CommandService depends on LLMStrategy, not directly on llm_module.py.
        self.llm_strategy = llm_strategy or GeminiLLMStrategy(use_mock=use_mock)

        # Keep use_mock for backward compatibility with existing tests/call sites.
        self.use_mock = use_mock

        # Command Pattern:
        # Store successfully executed commands for optional undo.
        self.command_history: list[Command] = []

    def handle_transcript(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Handle a full command pipeline from transcript to system decision.

        Returns:
            {
                "command_id": int,
                "ok": bool,
                "next_step": str,
                "response": str,
                "execution_status": str,
                "result": str,
                "command": dict | None
            }
        """
        start_time = time.time()

        llm_result = self.llm_strategy.parse_and_validate(
            transcript=transcript,
            sensor_data=sensor_data,
        )

        latency_ms = int((time.time() - start_time) * 1000)

        command = llm_result.get("command") or {}
        next_step = llm_result.get("next_step", "stop")

        validation_status = "passed" if llm_result.get("ok") else "failed"
        execution_status = "pending"
        result_status = "pending"
        error_message = llm_result.get("error")
        response_text = command.get("response", "Đang xử lý yêu cầu của bạn.")

        command_id = self._create_initial_log(
            transcript=transcript,
            command=command,
            result_status=result_status,
            validation_status=validation_status,
            execution_status=execution_status,
            latency_ms=latency_ms,
            error_message=error_message,
        )

        if next_step == "execute":
            success = self._execute_hardware_action(command)

            if success:
                result_status = "success"
                execution_status = "success"
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
            result_status = "success"
            execution_status = "clarify"

        elif next_step == "reject":
            result_status = llm_result.get("log_result") or "rejected: unknown_device"
            execution_status = "rejected"

        else:
            result_status = llm_result.get("log_result") or "fail: validation"
            execution_status = "failed"
            error_message = error_message or response_text

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
            "ok": bool(
                llm_result.get("ok")
                and execution_status in {"success", "waiting_auth", "clarify"}
            ),
            "next_step": next_step,
            "response": response_text,
            "execution_status": execution_status,
            "result": result_status,
            "command": command,
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

    def _execute_hardware_action(self, command_data: dict[str, Any]) -> bool:
        """
        Execute hardware action through Command Pattern.

        Flow:
            JSON command data
            -> create concrete Command object
            -> command.execute()
            -> hardware receiver executes actual payload
        """
        try:
            command = self.create_command(command_data)
            success = command.execute()

            if success:
                self.command_history.append(command)

            return success

        except Exception as exc:
            log_error("command_service", f"Hardware command execution failed: {exc}")
            return False