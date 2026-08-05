"""
Test cho luồng xác nhận khi hệ thống chỉ hỗ trợ MỘT PHẦN yêu cầu.

CHẠY OFFLINE: không gọi Gemini, LLM strategy được tiêm bản giả.

QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
---------------------------
    Một yêu cầu có phần không hỗ trợ nhưng phần còn lại hợp lệ thì KHÔNG được
    làm ngay, cũng KHÔNG được từ chối cả câu. Phải hỏi.

Ca gốc, quan sát được khi benchmark:

    "sau 5 phút nếu nhiệt độ trên 30 thì bật quạt phòng ngủ"
    -> reject: "Hệ thống không hỗ trợ thiết lập thời gian trễ."

Model hiểu đúng - hệ thống không có intent nào cho lịch trình. Nhưng nó đã
trích được điều kiện temperature > 30 rồi vứt đi, và người dùng phải gõ lại
từ đầu. Chạy ngay cũng sai: người dùng nói "sau 5 phút" mà nhận được một rule
tức thì thì đó là im lặng làm sai ý họ.
"""

from __future__ import annotations

from typing import Any

import pytest

from database import init_db as init_db_module
from services import logging_service as logging_service_module
from services import rule_service as rule_service_module
from services.command_service import CommandService
from services.rule_service import RuleService


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Mỗi test một database riêng.

    Dùng chung một file thì rule do test trước tạo còn nguyên ở test sau, và
    assert "chưa có rule nào" sẽ đỏ vì lý do không liên quan gì tới thứ đang
    kiểm tra.
    """
    test_db = tmp_path / "test_partial_intent.db"
    monkeypatch.setattr(init_db_module, "DB_PATH", test_db)
    monkeypatch.setattr(logging_service_module, "DB_PATH", test_db)
    monkeypatch.setattr(rule_service_module, "DB_PATH", test_db)
    init_db_module.init_db()
    yield


PARTIAL_COMMAND = {
    "intent": "create_rule",
    "action": "turn_on",
    "device": "fan",
    "room": "bedroom",
    "face_auth": False,
    "condition": {"sensor": "temperature", "operator": ">", "value": 30},
    "response": "Đã tạo luật.",
    "unsupported": "hẹn giờ 5 phút",
}

PLAIN_RULE = {**PARTIAL_COMMAND, "unsupported": None}


class FakeStrategy:
    """Trả một command đặt sẵn; đếm số lần được gọi."""

    def __init__(self, command: dict[str, Any], next_step: str = "create_rule") -> None:
        self.command = command
        self.next_step = next_step
        self.calls = 0

    def parse_and_validate(self, transcript, sensor_data=None, pending_command=None):
        self.calls += 1
        return {
            "ok": True,
            "transcript": transcript,
            "command": dict(self.command),
            "validation": {"passed": True, "code": "valid"},
            "next_step": self.next_step,
            "latency_ms": 0,
            "log_result": "success",
            "error": None,
        }


def build(command=PARTIAL_COMMAND) -> CommandService:
    return CommandService(
        rule_service=RuleService(),
        llm_strategy=FakeStrategy(command),
        use_mock=True,
    )


UTTERANCE = "sau 5 phút nếu nhiệt độ trên 30 thì bật quạt phòng ngủ"


# =============================================================================
# 1. Không làm ngay, không từ chối - phải hỏi
# =============================================================================

def test_partial_request_asks_instead_of_creating() -> None:
    service = build()

    result = service.handle_transcript(UTTERANCE, session_id="s1")

    assert result["result"] == "partial_intent: awaiting_confirmation"
    assert result["execution_status"] == "clarify"


def test_no_rule_is_created_before_the_user_answers() -> None:
    """
    Điểm mấu chốt. Tạo rule ngay nghĩa là bỏ qua mệnh đề người dùng vừa nói.
    """
    service = build()

    service.handle_transcript(UTTERANCE, session_id="s1")

    assert service.rule_service.get_active_rules() == []


def test_the_question_names_both_what_is_dropped_and_what_will_run() -> None:
    service = build()

    response = service.handle_transcript(UTTERANCE, session_id="s1")["response"]

    assert "hẹn giờ 5 phút" in response
    assert "nhiệt độ" in response
    assert "quạt phòng ngủ" in response
    assert response.rstrip().endswith("?")


# =============================================================================
# 2. Người dùng đồng ý
# =============================================================================

def test_yes_creates_the_rule() -> None:
    service = build()
    service.handle_transcript(UTTERANCE, session_id="s1")

    result = service.handle_transcript("có", session_id="s1")

    assert result["next_step"] == "create_rule"
    assert result["result"] == "success"
    assert len(service.rule_service.get_active_rules()) == 1


def test_confirmation_does_not_call_the_llm() -> None:
    """
    Quyết định Có/Không đối chiếu từ khoá phía server.

    Dữ liệu thật cho thấy vì sao: cùng chữ "có" từng cho ba kết quả khác nhau
    khi đi qua model. Ngoài ra mỗi lần gọi tốn một request trong hạn mức
    15/phút của gói free.
    """
    service = build()
    service.handle_transcript(UTTERANCE, session_id="s1")

    service.handle_transcript("có", session_id="s1")

    assert service.llm_strategy.calls == 1


def test_the_confirmed_command_is_the_one_that_was_validated() -> None:
    """
    KHÔNG dựng lại command từ transcript - transcript chứa cả phần không
    hỗ trợ. Dùng đúng command đã qua validator ở lượt trước.
    """
    service = build()
    service.handle_transcript(UTTERANCE, session_id="s1")

    result = service.handle_transcript("có", session_id="s1")

    assert result["command"]["condition"]["value"] == 30
    assert result["command"]["room"] == "bedroom"


# =============================================================================
# 3. Người dùng từ chối
# =============================================================================

def test_no_creates_nothing() -> None:
    service = build()
    service.handle_transcript(UTTERANCE, session_id="s1")

    result = service.handle_transcript("thôi", session_id="s1")

    assert result["next_step"] == "partial_intent_cancelled"
    assert result["result"] == "partial_intent: cancelled_by_user"
    assert service.rule_service.get_active_rules() == []


# =============================================================================
# 4. Không ép người dùng phải trả lời
# =============================================================================

def test_answering_with_something_else_falls_through_to_the_pipeline() -> None:
    service = build()
    service.handle_transcript(UTTERANCE, session_id="s1")

    service.handle_transcript("bật đèn phòng khách", session_id="s1")

    assert service.llm_strategy.calls == 2


def test_a_stale_confirmation_does_not_leak_into_a_new_session() -> None:
    service = build()
    service.handle_transcript(UTTERANCE, session_id="s1")

    result = service.handle_transcript("có", session_id="s2")

    assert result["result"] != "success"
    assert service.rule_service.get_active_rules() == []


# =============================================================================
# 5. Yêu cầu bình thường KHÔNG bị ảnh hưởng
# =============================================================================

def test_a_fully_supported_rule_is_created_immediately() -> None:
    """Không có unsupported -> đường cũ, không hỏi thêm gì."""
    service = build(PLAIN_RULE)

    result = service.handle_transcript(
        "nếu nhiệt độ trên 30 thì bật quạt phòng ngủ", session_id="s1"
    )

    assert result["result"] == "success"
    assert len(service.rule_service.get_active_rules()) == 1


def test_empty_unsupported_string_is_treated_as_absent() -> None:
    service = build({**PARTIAL_COMMAND, "unsupported": ""})

    result = service.handle_transcript(UTTERANCE, session_id="s1")

    assert result["result"] == "success"