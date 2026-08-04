from modules.llm_integration.llm_module import parse_and_validate


def test_turn_on_living_room_light():
    result = parse_and_validate("bật đèn phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["intent"] == "control_device"
    assert result["command"]["action"] == "turn_on"
    assert result["command"]["device"] == "light"
    assert result["command"]["room"] == "living_room"
    assert result["command"]["face_auth"] is False


def test_turn_off_bedroom_fan():
    result = parse_and_validate("tắt quạt phòng ngủ", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["action"] == "turn_off"
    assert result["command"]["device"] == "fan"
    assert result["command"]["room"] == "bedroom"


def test_open_main_door_requires_face_auth():
    result = parse_and_validate("mở cửa chính", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "auth_required"
    assert result["command"]["action"] == "open"
    assert result["command"]["device"] == "door"
    assert result["command"]["room"] == "main_door"
    assert result["command"]["face_auth"] is True


def test_close_main_door_no_face_auth():
    result = parse_and_validate("đóng cửa chính", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["action"] == "close"
    assert result["command"]["device"] == "door"
    assert result["command"]["room"] == "main_door"
    assert result["command"]["face_auth"] is False


def test_get_fan_status():
    result = parse_and_validate("quạt phòng ngủ đang thế nào", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["intent"] == "query_status"
    assert result["command"]["action"] == "get_status"
    assert result["command"]["device"] == "fan"
    assert result["command"]["room"] == "bedroom"


def test_ambiguous_command_is_clarify():
    result = parse_and_validate("bật cái đó lên", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"


def test_missing_room_is_clarify():
    result = parse_and_validate("bật đèn", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"
    assert result["command"]["action"] == "turn_on"
    assert result["command"]["device"] == "light"
    assert result["command"]["room"] is None


def test_missing_device_is_clarify():
    result = parse_and_validate("bật phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"
    assert result["command"]["action"] is None
    assert result["command"]["device"] is None
    assert result["command"]["room"] == "living_room"


def test_missing_action_is_clarify():
    result = parse_and_validate("đèn phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"
    assert result["command"]["action"] is None
    assert result["command"]["device"] == "light"
    assert result["command"]["room"] == "living_room"


def test_door_without_action_is_clarify():
    result = parse_and_validate("cửa chính", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"
    assert result["command"]["device"] == "door"
    assert result["command"]["room"] == "main_door"


def test_unknown_device_creates_registry_request():
    result = parse_and_validate("bật tivi phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "registry_request"
    assert result["command"]["intent"] == "registry_request"
    assert result["command"]["action"] is None
    assert result["command"]["device"] is None
    assert result["command"]["room"] is None


def test_unknown_room_and_device_creates_registry_request():
    result = parse_and_validate("bật máy lạnh phòng bếp", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "registry_request"
    assert result["command"]["intent"] == "registry_request"
    assert result["command"]["action"] is None
    assert result["command"]["device"] is None

    # HỢP ĐỒNG ĐÃ ĐỔI: yêu cầu đăng ký phải mang theo thứ CẦN đăng ký.
    # Trước đây cả ba slot đều None nên command_log chỉ còn transcript thô,
    # quản trị viên đọc bảng không biết phải thêm gì:
    #     (1902, 'nhà bếp', 'registry_request', 'waiting_admin_review')
    assert result["command"]["room"] == "kitchen"


def test_create_rule_success():
    result = parse_and_validate(
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách",
        use_mock=True,
    )

    assert result["ok"] is True
    assert result["next_step"] == "create_rule"
    assert result["command"]["intent"] == "create_rule"
    assert result["command"]["condition"]["sensor"] == "temperature"
    assert result["command"]["condition"]["operator"] == ">"
    assert result["command"]["condition"]["value"] == 30


def test_create_rule_missing_room_is_clarify():
    result = parse_and_validate(
        "nếu nhiệt độ trên 30 độ thì bật quạt",
        use_mock=True,
    )

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"
    assert result["command"]["device"] == "fan"
    assert result["command"]["room"] is None


def test_create_rule_missing_condition_is_clarify():
    result = parse_and_validate(
        "nếu trời nóng thì bật quạt phòng khách",
        use_mock=True,
    )

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"

def test_unknown_device_creates_registry_request():
    result = parse_and_validate("bật tivi phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "registry_request"
    assert result["command"]["intent"] == "registry_request"


def test_unknown_room_creates_registry_request():
    result = parse_and_validate("bật đèn phòng làm việc", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "registry_request"
    assert result["command"]["intent"] == "registry_request"


def test_missing_room_is_clarify():
    result = parse_and_validate("bật đèn", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"
    assert result["command"]["device"] == "light"
    assert result["command"]["room"] is None


def test_custom_registry_supports_new_room_and_device():
    custom_registry = {
        "living_room": {
            "light": ["turn_on", "turn_off", "get_status"],
            "tv": ["turn_on", "turn_off", "get_status"],
        },
        "office": {
            "light": ["turn_on", "turn_off", "get_status"],
        },
    }

    result = parse_and_validate(
        "bật tivi phòng khách",
        device_registry=custom_registry,
        use_mock=True,
    )

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["device"] == "tv"
    assert result["command"]["room"] == "living_room"

    result = parse_and_validate(
        "bật đèn phòng làm việc",
        device_registry=custom_registry,
        use_mock=True,
    )

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["device"] == "light"
    assert result["command"]["room"] == "office"