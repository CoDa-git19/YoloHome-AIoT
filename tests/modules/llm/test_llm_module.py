from modules.llm_integration.llm_module import parse_and_validate


def test_mock_create_automation_rule():
    """Kiểm tra Mock Parser nhận diện chính xác cấu trúc tạo quy tắc (create_rule)"""
    result = parse_and_validate(
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách", use_mock=True
    )

    assert result["ok"] is True
    assert result["next_step"] == "create_rule"
    assert result["command"]["intent"] == "create_rule"
    assert result["command"]["device"] == "fan"
    assert result["command"]["action"] == "turn_on"
    assert result["command"]["condition"] is not None
    assert result["command"]["condition"]["sensor"] == "temperature"
    assert result["command"]["condition"]["operator"] == ">"
    assert result["command"]["condition"]["value"] == 30


def test_get_fan_status():
    """Kiểm tra lệnh truy vấn trạng thái thiết bị (query_status)"""
    result = parse_and_validate("quạt phòng ngủ đang thế nào", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["intent"] == "query_status"
    assert result["command"]["action"] == "get_status"
    assert result["command"]["device"] == "fan"
    assert result["command"]["room"] == "bedroom"


def test_close_door_no_face_auth():
    """Kiểm tra lệnh đóng cửa: Passed và không yêu cầu face_auth"""
    result = parse_and_validate("đóng cửa chính", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["intent"] == "control_device"
    assert result["command"]["action"] == "close"
    assert result["command"]["device"] == "door"
    assert result["command"]["face_auth"] is False

def test_open_door_requires_auth_next_step():
    result = parse_and_validate("mở cửa chính", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "auth_required"
    assert result["command"]["intent"] == "control_device"
    assert result["command"]["action"] == "open"
    assert result["command"]["device"] == "door"
    assert result["command"]["room"] == "main_door"
    assert result["command"]["face_auth"] is True