from modules.llm_integration.llm_module import build_prompt


def test_build_prompt_contains_transcript():
    prompt = build_prompt("bật đèn phòng khách")

    assert "bật đèn phòng khách" in prompt
    assert "Device registry:" in prompt
    assert "Current sensor data:" in prompt


def test_build_prompt_contains_device_registry():
    prompt = build_prompt("mở cửa chính")

    assert "living_room" in prompt
    assert "bedroom" in prompt
    assert "main_door" in prompt
    assert "light" in prompt
    assert "fan" in prompt
    assert "door" in prompt