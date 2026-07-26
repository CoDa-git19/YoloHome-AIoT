"""
Tests cho hợp đồng interface.

Hai hợp đồng bị đổi trong quá trình refactor, và cả hai đều HỎNG ÂM THẦM
hoặc hỏng muộn nếu ai đó không tuân thủ:

1. LLMStrategy.parse_and_validate() thêm pending_command (P1-3).
   -> Strategy cũ ném TypeError, nhưng chỉ ném GIỮA LÚC CHẠY.

2. Hardware phải trả "state" cho action=get_status (P1-4).
   -> Thiếu thì không crash, chỉ là người dùng luôn nhận
      "không xác định được trạng thái". Không ai biết cho tới lúc demo.

File này bắt cả hai NGAY LÚC KHỞI ĐỘNG.
"""

from __future__ import annotations

import contextlib
import io
import logging
from typing import Any

import pytest

from modules.llm_integration.llm_strategy import GeminiLLMStrategy, MockLLMStrategy
from services.command_service import CommandService, MockHardwareModule
from system_core.contracts import (
    VALID_DEVICE_STATES,
    ContractError,
    check_status_result,
    verify_hardware_receiver,
    verify_llm_strategy,
)
from system_core.strategies import LLMStrategy


def quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


# =============================================================================
# 1. Hợp đồng LLMStrategy
# =============================================================================

class GoodStrategy(LLMStrategy):
    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
        pending_command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {}


class KwargsStrategy(LLMStrategy):
    """Nuốt hết bằng **kwargs cũng là hợp lệ."""

    def parse_and_validate(self, transcript: str, **kwargs: Any) -> dict[str, Any]:
        return {}


class OutdatedStrategy(LLMStrategy):
    """Strategy viết trước P1-3, thiếu pending_command."""

    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {}


def test_conforming_strategy_passes():
    verify_llm_strategy(GoodStrategy())


def test_kwargs_strategy_passes():
    verify_llm_strategy(KwargsStrategy())


def test_outdated_strategy_is_rejected():
    with pytest.raises(ContractError, match="pending_command"):
        verify_llm_strategy(OutdatedStrategy())


def test_object_without_parse_and_validate_is_rejected():
    with pytest.raises(ContractError, match="parse_and_validate"):
        verify_llm_strategy(object())


def test_command_service_rejects_outdated_strategy_at_startup():
    """
    Quan trọng: lỗi phải xuất hiện lúc KHỞI TẠO CommandService, không phải
    giữa lúc người dùng đang nói chuyện với bot.
    """
    with pytest.raises(ContractError):
        CommandService(llm_strategy=OutdatedStrategy())


@pytest.mark.parametrize(
    "strategy_factory",
    [
        lambda: MockLLMStrategy(),
        lambda: GeminiLLMStrategy(use_mock=True),
    ],
)
def test_shipped_strategies_conform(strategy_factory):
    """Các strategy thật của dự án phải tuân thủ hợp đồng."""
    verify_llm_strategy(strategy_factory())


def test_every_llm_strategy_subclass_conforms():
    """
    Quét MỌI subclass của LLMStrategy đang tồn tại.

    Ai thêm strategy mới mà quên pending_command sẽ bị bắt ở đây, kể cả khi
    họ không viết test nào cho nó.
    """
    known_bad = {"OutdatedStrategy"}

    for subclass in LLMStrategy.__subclasses__():
        if subclass.__name__ in known_bad:
            continue

        if getattr(subclass, "__abstractmethods__", None):
            continue

        verify_llm_strategy(subclass.__new__(subclass))


# =============================================================================
# 2. Hợp đồng hardware
# =============================================================================

def test_mock_hardware_conforms():
    verify_hardware_receiver(MockHardwareModule())


def test_object_without_execute_command_is_rejected():
    with pytest.raises(ContractError, match="execute_command"):
        verify_hardware_receiver(object())


def test_command_service_rejects_bad_hardware_at_startup():
    with pytest.raises(ContractError):
        CommandService(use_mock=True, hardware_module=object())


@pytest.mark.parametrize("state", sorted(VALID_DEVICE_STATES))
def test_valid_states_produce_no_warning(caplog, state: str):
    with caplog.at_level(logging.ERROR):
        check_status_result(
            hardware_name="Fake",
            command={"action": "get_status"},
            result={"status": "success", "state": state},
        )

    assert caplog.records == []


def test_missing_state_is_logged(caplog):
    """
    Đây là kiểu hỏng tệ nhất: không exception, không crash, hệ thống vẫn
    "chạy" - chỉ là người dùng không bao giờ biết cái quạt bật hay tắt.
    """
    with caplog.at_level(logging.ERROR):
        check_status_result(
            hardware_name="TeammateHardware",
            command={"action": "get_status"},
            result={"status": "success"},
        )

    assert len(caplog.records) == 1
    assert "state" in caplog.text
    assert "TeammateHardware" in caplog.text


def test_invalid_state_value_is_logged(caplog):
    """'ON' hoa không hợp lệ. Chỉ chấp nhận 'on'."""
    with caplog.at_level(logging.ERROR):
        check_status_result(
            hardware_name="Fake",
            command={"action": "get_status"},
            result={"status": "success", "state": "ON"},
        )

    assert len(caplog.records) == 1
    assert "ON" in caplog.text


def test_non_dict_result_is_logged(caplog):
    with caplog.at_level(logging.ERROR):
        check_status_result(
            hardware_name="Fake",
            command={"action": "get_status"},
            result=True,
        )

    assert len(caplog.records) == 1


def test_control_actions_are_not_checked(caplog):
    """Hợp đồng "state" chỉ áp dụng cho get_status."""
    with caplog.at_level(logging.ERROR):
        check_status_result(
            hardware_name="Fake",
            command={"action": "turn_on"},
            result={"status": "success"},
        )

    assert caplog.records == []


def test_failed_command_is_not_checked(caplog):
    """Lệnh thất bại thì không đòi "state" - đã có lỗi khác rồi."""
    with caplog.at_level(logging.ERROR):
        check_status_result(
            hardware_name="Fake",
            command={"action": "get_status"},
            result={"status": "error", "message": "serial timeout"},
        )

    assert caplog.records == []


# =============================================================================
# 3. End-to-end: vi phạm hợp đồng vẫn không làm sập hệ thống
# =============================================================================

def test_hardware_without_state_logs_but_still_responds(caplog):
    class StatelessHardware:
        def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
            return {"status": "success"}

    service = CommandService(use_mock=True, hardware_module=StatelessHardware())

    with caplog.at_level(logging.ERROR):
        result = quiet(service.handle_transcript, "quạt phòng ngủ đang thế nào")

    # Vẫn trả lời tử tế, không crash...
    assert result["ok"] is True
    assert "None" not in result["response"]

    # ...nhưng KHÔNG im lặng nữa.
    assert any("HỢP ĐỒNG" in record.message for record in caplog.records)


def test_compliant_hardware_produces_no_contract_errors(caplog):
    service = CommandService(use_mock=True)

    with caplog.at_level(logging.ERROR):
        quiet(service.handle_transcript, "bật quạt phòng ngủ")
        result = quiet(service.handle_transcript, "quạt phòng ngủ đang thế nào")

    assert result["response"] == "Quạt ở phòng ngủ đang bật."
    assert not any("HỢP ĐỒNG" in record.message for record in caplog.records)