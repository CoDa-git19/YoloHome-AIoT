"""
Test cho luồng xác nhận Có/Không của yêu cầu đăng ký.

CHẠY OFFLINE: không gọi Gemini, không cần database.

HAI QUY TẮC ĐƯỢC KHOÁ LẠI Ở ĐÂY
-------------------------------
1. Quyết định Có/Không do SERVER đối chiếu từ khoá, KHÔNG hỏi lại LLM.
2. Lượt trả lời xác nhận KHÔNG tiêu vào SESSION_MAX_TURNS.

Bằng chứng từ command_log thật:

    (1893, 'có', 'registry_request', 'waiting_admin_review')
    (1903, 'có', 'clarify',          'success')
    (1905, 'có', 'clarify',          'success')

Cùng một chữ "có", ba kết quả khác nhau - model không nhất quán trên một câu
trả lời Yes/No, vốn là dữ liệu có cấu trúc chứ không phải ngôn ngữ cần diễn giải.

    (1892, 'ok', 'failed', 'fail: clarify_limit')
    (1904, 'ok', 'failed', 'fail: clarify_limit')

Cả hai lần chạm giới hạn lượt đều do câu đồng ý, không phải do câu lệnh mơ hồ.
"""

from __future__ import annotations

import pytest

from modules.llm_integration.llm_module import detect_confirmation
from services.session_service import SessionService


# =============================================================================
# 1. Nhận diện đồng ý / từ chối
# =============================================================================

@pytest.mark.parametrize(
    "text", ["có", "ừ", "ok", "oke", "đồng ý", "vâng", "dạ", "được", "làm đi"]
)
def test_affirmatives_are_recognised(text: str) -> None:
    assert detect_confirmation(text) == "yes"


@pytest.mark.parametrize(
    "text", ["không", "ko", "thôi", "khỏi", "đừng", "hủy", "bỏ qua", "không cần"]
)
def test_negatives_are_recognised(text: str) -> None:
    assert detect_confirmation(text) == "no"


@pytest.mark.parametrize("text", ["Có", "CÓ", "  có  ", "Có.", "ok!", "Đồng ý!"])
def test_recognition_ignores_case_and_punctuation(text: str) -> None:
    assert detect_confirmation(text) == "yes"


# =============================================================================
# 2. Khớp TOÀN CÂU, không phải chuỗi con
# =============================================================================

@pytest.mark.parametrize(
    "text",
    [
        "nếu có người thì bật đèn phòng khách",
        "có người trong phòng khách không",
        "bật đèn",
        "phòng khách",
        "không khí trong phòng thế nào",
    ],
)
def test_confirmation_words_inside_a_real_command_are_ignored(text: str) -> None:
    """
    "có" là từ rất phổ biến. "nếu CÓ người thì bật đèn" là một luật tự động
    hoá, không phải lời đồng ý. Khớp chuỗi con sẽ nuốt mất luật đó.
    """
    assert detect_confirmation(text) is None


@pytest.mark.parametrize("text", ["", "   ", "asdf", "123"])
def test_empty_or_unrelated_returns_none(text: str) -> None:
    assert detect_confirmation(text) is None


# =============================================================================
# 3. SessionService: yêu cầu đăng ký tách khỏi câu lệnh dở dang
# =============================================================================

@pytest.fixture
def session() -> SessionService:
    return SessionService(ttl_seconds=60.0, max_turns=3)


PAYLOAD = {"room": "kitchen", "device": "light", "action": "turn_on", "command_id": 42}


def test_pending_registry_round_trip(session: SessionService) -> None:
    session.set_pending_registry("s1", PAYLOAD)

    assert session.get_pending_registry("s1") == PAYLOAD


def test_pending_registry_is_isolated_per_session(session: SessionService) -> None:
    session.set_pending_registry("s1", PAYLOAD)

    assert session.get_pending_registry("s2") is None


def test_clear_registry_keeps_the_pending_command(session: SessionService) -> None:
    """
    Xoá yêu cầu đăng ký KHÔNG được xoá câu lệnh đang dở.

    Người dùng trả lời "có" xong vẫn đang muốn bật đèn - bắt họ nói lại từ
    đầu là mất hết ngữ cảnh.
    """
    session.set_pending("s1", {"device": "light", "room": None})
    session.set_pending_registry("s1", PAYLOAD)

    session.clear_registry("s1")

    assert session.get_pending_registry("s1") is None
    assert session.get_pending("s1") is not None


def test_registry_payload_is_copied_not_shared(session: SessionService) -> None:
    payload = dict(PAYLOAD)
    session.set_pending_registry("s1", payload)
    payload["room"] = "bathroom"

    assert session.get_pending_registry("s1")["room"] == "kitchen"


def test_missing_session_id_is_a_no_op(session: SessionService) -> None:
    session.set_pending_registry(None, PAYLOAD)

    assert session.get_pending_registry(None) is None


# =============================================================================
# 4. Lượt xác nhận không tiêu vào giới hạn lượt
# =============================================================================

def test_touch_refreshes_without_counting_a_turn(session: SessionService) -> None:
    session.set_pending("s1", {"device": "light", "room": None})
    turns_before = session.get_turns("s1")

    session.touch("s1")

    assert session.get_turns("s1") == turns_before


def test_confirmation_does_not_exhaust_the_turn_limit(session: SessionService) -> None:
    """
    Kịch bản thật: bật đèn -> nhà bếp -> có -> phòng khách.

    Nếu lượt "có" tính là một lần hỏi lại, người dùng chạm giới hạn trước khi
    kịp trả lời câu hỏi thật.
    """
    session.set_pending("s1", {"device": "light", "room": None})   # lượt 1
    session.set_pending("s1", {"device": "light", "room": None})   # lượt 2

    session.touch("s1")  # người dùng trả lời "có"

    assert session.get_turns("s1") == 2
    assert not session.has_reached_turn_limit("s1")


def test_clear_all_wipes_registries_too(session: SessionService) -> None:
    session.set_pending_registry("s1", PAYLOAD)

    session.clear_all()

    assert session.get_pending_registry("s1") is None


def test_expired_registry_is_dropped() -> None:
    expired = SessionService(ttl_seconds=0.001, max_turns=3)
    expired.set_pending_registry("s1", PAYLOAD)

    import time

    time.sleep(0.01)

    assert expired.get_pending_registry("s1") is None