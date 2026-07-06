from database.init_db import init_db
from modules.llm_integration.llm_module import parse_and_validate
from services.logging_service import log_command


def setup_module():
    """Ensure database tables exist before integration tests run."""
    init_db()


def test_command_pipeline_execute_flow():
    """Kiểm tra luồng pipeline cho lệnh điều khiển thông thường."""
    transcript = "bật đèn phòng khách"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "execute"
    assert llm_result["command"]["device"] == "light"
    assert llm_result["command"]["action"] == "turn_on"

    row_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="success",
        validation_status="passed",
        execution_status="success",
        latency_ms=llm_result["latency_ms"],
    )

    assert row_id > 0


def test_command_pipeline_auth_required_flow():
    """Kiểm tra luồng pipeline yêu cầu xác thực khuôn mặt."""
    transcript = "mở cửa chính"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "auth_required"
    assert llm_result["command"]["device"] == "door"
    assert llm_result["command"]["action"] == "open"
    assert llm_result["command"]["face_auth"] is True

    row_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="waiting_auth",
        validation_status="passed",
        execution_status="waiting_auth",
        latency_ms=llm_result["latency_ms"],
    )

    assert row_id > 0


def test_command_pipeline_create_rule_flow():
    """Kiểm tra luồng pipeline tạo luật tự động hóa."""
    transcript = "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "create_rule"
    assert llm_result["command"]["intent"] == "create_rule"
    assert llm_result["command"]["condition"]["sensor"] == "temperature"

    row_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="success",
        validation_status="passed",
        execution_status="success",
        latency_ms=llm_result["latency_ms"],
    )

    assert row_id > 0


def test_command_pipeline_reject_flow():
    """Kiểm tra luồng pipeline từ chối lệnh không hỗ trợ."""
    transcript = "bật máy lạnh phòng bếp"

    llm_result = parse_and_validate(transcript, use_mock=True)

    assert llm_result["ok"] is True
    assert llm_result["next_step"] == "reject"
    assert llm_result["command"]["intent"] == "reject"

    row_id = log_command(
        transcript=transcript,
        json_cmd=llm_result["command"],
        result="rejected: unknown_device",
        validation_status="passed",
        execution_status="rejected",
        latency_ms=llm_result["latency_ms"],
    )

    assert row_id > 0