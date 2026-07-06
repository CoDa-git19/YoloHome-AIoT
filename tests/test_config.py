from config.settings import DEVICE_REGISTRY_PATH, COMMAND_SCHEMA_PATH, PROMPT_TEMPLATE_PATH
from modules.llm_integration.llm_module import load_json


def test_config_files_exist():
    assert DEVICE_REGISTRY_PATH.exists()
    assert COMMAND_SCHEMA_PATH.exists()
    assert PROMPT_TEMPLATE_PATH.exists()


def test_device_registry_loads():
    registry = load_json(DEVICE_REGISTRY_PATH)

    assert "living_room" in registry
    assert "bedroom" in registry
    assert "main_door" in registry