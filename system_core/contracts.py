from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ContractError(TypeError):
    """Một thành phần không tuân thủ hợp đồng interface."""


# =============================================================================
# Hợp đồng 1: LLMStrategy
# =============================================================================

REQUIRED_STRATEGY_PARAMS = ("transcript", "sensor_data", "pending_command")


def verify_llm_strategy(strategy: Any) -> None:
    """
    Kiểm tra strategy có đúng chữ ký mà CommandService sẽ gọi.

    Chữ ký của LLMStrategy đã đổi ở P1-3 (thêm pending_command cho hội thoại
    nhiều lượt). Strategy cũ thiếu tham số này sẽ ném TypeError - nhưng chỉ
    ném GIỮA LÚC CHẠY, khi có người thật đang nói chuyện với bot.

    Kiểm tra ngay lúc khởi động thì lỗi hiện ra trước, không phải giữa demo.
    """
    method = getattr(strategy, "parse_and_validate", None)

    if not callable(method):
        raise ContractError(
            f"{type(strategy).__name__} không có parse_and_validate(). "
            "Mọi LLM strategy phải kế thừa LLMStrategy."
        )

    signature = inspect.signature(method)
    parameters = signature.parameters

    # Chấp nhận **kwargs: strategy nào nuốt hết cũng là hợp lệ.
    accepts_kwargs = any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in parameters.values()
    )
    if accepts_kwargs:
        return

    missing = [name for name in REQUIRED_STRATEGY_PARAMS if name not in parameters]

    if missing:
        raise ContractError(
            f"{type(strategy).__name__}.parse_and_validate() thiếu tham số: "
            f"{', '.join(missing)}.\n"
            "Chữ ký đúng:\n"
            "    def parse_and_validate(\n"
            "        self,\n"
            "        transcript: str,\n"
            "        sensor_data: dict | None = None,\n"
            "        pending_command: dict | None = None,\n"
            "    ) -> dict:"
        )


# =============================================================================
# Hợp đồng 2: hardware receiver
# =============================================================================

VALID_DEVICE_STATES = {"on", "off", "open", "closed"}


def verify_hardware_receiver(hardware: Any) -> None:
    """Kiểm tra hardware module có execute_command()."""
    method = getattr(hardware, "execute_command", None)

    if not callable(method):
        raise ContractError(
            f"{type(hardware).__name__} không có execute_command(command: dict) -> dict."
        )


def check_status_result(
    hardware_name: str,
    command: dict[str, Any],
    result: Any,
) -> None:
    """
    Cảnh báo khi phần cứng vi phạm hợp đồng get_status.

    HỢP ĐỒNG:
        execute_command({"action": "get_status", ...})
        -> {"status": "success", "state": "on"|"off"|"open"|"closed"}

    Thiếu "state" thì hệ thống KHÔNG crash - nó vẫn trả lời người dùng
    "không xác định được trạng thái". Đó chính là vấn đề: hỏng âm thầm,
    không ai biết cho tới khi đứng trước hội đồng.

    Nên phải la lên.
    """
    if command.get("action") != "get_status":
        return

    if not isinstance(result, dict):
        logger.error(
            "VI PHẠM HỢP ĐỒNG: %s.execute_command() trả về %s thay vì dict.",
            hardware_name,
            type(result).__name__,
        )
        return

    if result.get("status") != "success":
        return

    state = result.get("state")

    if state is None:
        logger.error(
            "VI PHẠM HỢP ĐỒNG: %s.execute_command(action=get_status) không trả "
            'về "state". Người dùng sẽ luôn nhận "không xác định được trạng thái". '
            'Phần cứng phải trả {"status": "success", "state": "on"|"off"|"open"|"closed"}.',
            hardware_name,
        )
        return

    if state not in VALID_DEVICE_STATES:
        logger.error(
            'VI PHẠM HỢP ĐỒNG: %s trả về state=%r không hợp lệ. '
            "Chỉ chấp nhận: %s.",
            hardware_name,
            state,
            ", ".join(sorted(VALID_DEVICE_STATES)),
        )


__all__ = [
    "ContractError",
    "VALID_DEVICE_STATES",
    "check_status_result",
    "verify_hardware_receiver",
    "verify_llm_strategy",
]