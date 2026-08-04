from config.settings import LANGUAGE_ALIASES_PATH
from modules.llm_integration.llm_module import (
    load_language_aliases,
    parse_and_validate,
)


def test_language_aliases_file_exists():
    assert LANGUAGE_ALIASES_PATH.exists()


def test_language_aliases_have_required_sections():
    aliases = load_language_aliases()

    assert "actions" in aliases
    assert "rooms" in aliases
    assert "devices" in aliases
    assert "display_names" in aliases
    assert "conditions" in aliases


def test_mock_parser_uses_alias_config_for_supported_command():
    result = parse_and_validate("bật đèn phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["device"] == "light"
    assert result["command"]["room"] == "living_room"
    assert result["command"]["action"] == "turn_on"


def test_mock_parser_uses_alias_config_for_registry_request():
    result = parse_and_validate("bật tivi phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "registry_request"
    assert result["command"]["intent"] == "registry_request"


def test_mock_parser_detects_condition_from_alias_config():
    result = parse_and_validate(
        "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách",
        use_mock=True,
    )

    assert result["ok"] is True
    assert result["next_step"] == "create_rule"
    assert result["command"]["condition"] == {
        "sensor": "temperature",
        "operator": ">",
        "value": 30,
    }

def test_supported_device_executes():
    result = parse_and_validate("bật đèn phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "execute"
    assert result["command"]["device"] == "light"


def test_known_unsupported_device_returns_registry_request():
    result = parse_and_validate("bật tivi phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "registry_request"
    assert result["command"]["intent"] == "registry_request"


def test_unknown_device_returns_clarify_not_registry_request():
    result = parse_and_validate("bật robot phòng khách", use_mock=True)

    assert result["ok"] is True
    assert result["next_step"] == "clarify"
    assert result["command"]["intent"] == "clarify"