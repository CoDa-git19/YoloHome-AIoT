from config import settings


def test_llm_runtime_config_exists():
    assert isinstance(settings.USE_MOCK_LLM, bool)
    assert isinstance(settings.GEMINI_API_KEY, str)
    assert isinstance(settings.GEMINI_MODEL, str)
    assert settings.GEMINI_MODEL


def test_face_auth_threshold_is_float():
    assert isinstance(settings.FACE_AUTH_THRESHOLD, float)
    assert 0.0 <= settings.FACE_AUTH_THRESHOLD <= 1.0


def test_core_config_paths_exist():
    assert settings.DEVICE_REGISTRY_PATH.exists()
    assert settings.COMMAND_SCHEMA_PATH.exists()
    assert settings.PROMPT_TEMPLATE_PATH.exists()


def test_device_capabilities_path_exists():
    assert settings.DEVICE_CAPABILITIES_PATH.exists()


def test_language_aliases_path_exists():
    assert settings.LANGUAGE_ALIASES_PATH.exists()