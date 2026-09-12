from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ContractError(TypeError):
    """Một thành phần không tuân thủ hợp đồng interface."""


def _record(module: str, message: str) -> None:
    """
    Ghi một vi phạm hợp đồng ra CẢ HAI kênh.

    logger.error()  -> hiện ngay trên console lúc đang chạy.
    log_error()     -> lưu vào bảng error_log, truy lại được sau khi tắt máy.

    Chỉ ghi console là chưa đủ: không entry point nào cấu hình logging, nên
    dòng log biến mất cùng cửa sổ terminal. Vi phạm hợp đồng là loại lỗi âm
    thầm nhất trong hệ thống nên nó phải nằm trong database.

    Import cục bộ để system_core không phụ thuộc services lúc import module.
    """
    logger.error(message)

    try:
        from services.logging_service import log_error

        log_error(module, message)
    except Exception as exc:  # noqa: BLE001
        # Không được ném ngược lên: hàm này chạy trong đường đi của lệnh thật.
        logger.error("Không ghi được error_log: %s", exc)


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

    Nên phải la lên - và la vào chỗ nghe lại được, tức là error_log.
    """
    if command.get("action") != "get_status":
        return

    if not isinstance(result, dict):
        _record(
            "contract",
            f"VI PHẠM HỢP ĐỒNG: {hardware_name}.execute_command() trả về "
            f"{type(result).__name__} thay vì dict.",
        )
        return

    if result.get("status") != "success":
        return

    state = result.get("state")

    if state is None:
        _record(
            "contract",
            f"VI PHẠM HỢP ĐỒNG: {hardware_name}.execute_command(action=get_status) "
            'không trả về "state". Người dùng sẽ luôn nhận "không xác định được '
            'trạng thái". Phần cứng phải trả {"status": "success", '
            '"state": "on"|"off"|"open"|"closed"}.',
        )
        return

    if state not in VALID_DEVICE_STATES:
        _record(
            "contract",
            f"VI PHẠM HỢP ĐỒNG: {hardware_name} trả về state={state!r} không hợp lệ. "
            f"Chỉ chấp nhận: {', '.join(sorted(VALID_DEVICE_STATES))}.",
        )


__all__ = [
    "ContractError",
    "VALID_DEVICE_STATES",
    "check_status_result",
    "verify_hardware_receiver",
    "verify_llm_strategy",
]