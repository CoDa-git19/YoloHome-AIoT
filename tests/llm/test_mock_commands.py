from modules.llm_integration.llm_module import parse_and_validate


def test_turn_on_living_room_light():
    result = parse_and_validate("bật đèn phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["command"]["intent"] == "control_device"
    assert result["command"]["action"] == "turn_on"
    assert result["command"]["device"] == "light"
    assert result["command"]["room"] == "living_room"
    assert result["command"]["face_auth"] is False


def test_turn_off_bedroom_fan():
    result = parse_and_validate("tắt quạt phòng ngủ", use_mock=True)

    assert result["ok"] is True
    assert result["command"]["action"] == "turn_off"
    assert result["command"]["device"] == "fan"
    assert result["command"]["room"] == "bedroom"


def test_open_main_door_requires_face_auth():
    result = parse_and_validate("mở cửa chính", use_mock=True)

    assert result["ok"] is True
    assert result["command"]["action"] == "open"
    assert result["command"]["device"] == "door"
    assert result["command"]["room"] == "main_door"
    assert result["command"]["face_auth"] is True


def test_unsupported_room_or_device_is_rejected():
    result = parse_and_validate("bật máy lạnh phòng bếp", use_mock=True)

    assert result["ok"] is True
    assert result["command"]["intent"] == "reject"


def test_ambiguous_command_is_clarify():
    result = parse_and_validate("bật cái đó lên", use_mock=True)

    assert result["ok"] is True
    assert result["command"]["intent"] == "clarify"