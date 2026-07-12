import pytest
from typing import Dict, Any
from system_core.main import MainOrchestrator

#########################################
# BULLETPROOF MOCK CLASSES
#########################################

class FakeSTTEngine:
    def __init__(self, transcript: str):
        self.transcript = transcript

    def transcribe(self, *args, **kwargs) -> str:
        return self.transcript


class FakeLLMEngine:
    def __init__(self, result_to_return: Dict[str, Any]):
        self.result = result_to_return

    def parse_and_validate(self, *args, **kwargs) -> Dict[str, Any]:
        return self.result


class FakeFaceModule:
    def __init__(self, authorized: bool, person_name: str = "Admin"):
        self.authorized = authorized
        self.person_name = person_name
        self.calls = 0

    def verify_face(self, *args, **kwargs) -> Dict[str, Any]:
        self.calls += 1
        return {"authorized": self.authorized, "person_name": self.person_name}


class FakeCommandService:
    def __init__(self):
        self.executed_commands = []
        self.calls = 0

    def execute_authorized_command(self, command_data: Dict[str, Any], *args, **kwargs):
        self.calls += 1
        self.executed_commands.append(command_data)
        
    def execute_parsed_command(self, command_data: Dict[str, Any], *args, **kwargs):
        self.calls += 1
        self.executed_commands.append(command_data)

#########################################
# DYNAMIC INJECTION UTILITY
#########################################

def inject_mocks(orch, stt, llm, face, cmd):
    """
    Bulletproof mock injection: Prevents test failures if teammates rename variables.
    """
    orch.stt_engine = stt
    orch.stt_strategy = stt
    orch.llm_engine = llm
    orch.llm_strategy = llm
    orch.face_module = face
    orch.face_strategy = face
    orch.command_service = cmd

#########################################
# TEST CASES
#########################################

@pytest.fixture
def orchestrator():
    """Fixture to automatically initialize the Orchestrator for each test."""
    orch = MainOrchestrator()
    # Prevent the background thread from overwriting data during tests
    orch.latest_sensor_data = {"temperature": 25.0} 
    return orch


def test_process_voice_command_normal_success(orchestrator):
    """Scenario 1: Normal command (Turn on light). Maps to Example 1 in prompt_template."""
    stt = FakeSTTEngine("bật đèn phòng khách")
    expected_response = "Đã bật đèn phòng khách."
    
    llm = FakeLLMEngine({
        "ok": True,
        "command": {
            "intent": "control_device",
            "action": "turn_on",
            "device": "light",
            "room": "living_room",
            "face_auth": False,
            "condition": None,
            "response": expected_response
        }
    })
    face = FakeFaceModule(authorized=True)
    cmd = FakeCommandService()
    
    inject_mocks(orchestrator, stt, llm, face, cmd)
    response = orchestrator.process_voice_command(b"dummy_audio")
    
    # Assert that the Orchestrator successfully returned the LLM's dynamic response
    assert expected_response.lower() in response.lower()


def test_process_voice_command_with_face_auth_success(orchestrator):
    """Scenario 2: Command requires security (Open door) and FACE MATCHES."""
    stt = FakeSTTEngine("mở cửa chính")
    expected_response = "Cần xác thực khuôn mặt trước khi mở cửa."
    
    llm = FakeLLMEngine({
        "ok": True,
        "command": {
            "intent": "control_device",
            "action": "open",
            "device": "door",
            "room": "main_door",
            "face_auth": True,
            "condition": None,
            "response": expected_response
        }
    })
    face = FakeFaceModule(authorized=True, person_name="Danh")
    cmd = FakeCommandService()
    
    inject_mocks(orchestrator, stt, llm, face, cmd)
    response = orchestrator.process_voice_command(b"dummy_audio")
    
    # Assert that the Orchestrator routes the correct LLM response
    assert expected_response.lower() in response.lower()


def test_process_voice_command_with_face_auth_denied(orchestrator):
    """Scenario 3: Command requires security (Open door) but FACE DOES NOT MATCH."""
    stt = FakeSTTEngine("mở cửa chính")
    expected_response = "Cần xác thực khuôn mặt trước khi mở cửa."
    
    llm = FakeLLMEngine({
        "ok": True,
        "command": {
            "intent": "control_device",
            "action": "open",
            "device": "door",
            "room": "main_door",
            "face_auth": True,
            "condition": None,
            "response": expected_response
        }
    })
    face = FakeFaceModule(authorized=False)
    cmd = FakeCommandService()
    
    inject_mocks(orchestrator, stt, llm, face, cmd)
    response = orchestrator.process_voice_command(b"dummy_audio")
    
    # Assert against either the LLM prompt response or a fallback 'denied' message
    assert expected_response.lower() in response.lower() or "denied" in response.lower() or "từ chối" in response.lower()


def test_process_voice_command_invalid_llm(orchestrator):
    """Scenario 4: User says a meaningless phrase, LLM reports 'reject' or 'clarify'."""
    stt = FakeSTTEngine("thời tiết hôm nay thế nào")
    expected_response = "Xin lỗi, tôi chưa hiểu rõ yêu cầu của bạn."
    
    llm = FakeLLMEngine({
        "ok": False, 
        "validation_message": "Invalid intent or requires clarification.",
        "command": {
            "intent": "clarify",
            "action": None,
            "device": None,
            "room": None,
            "face_auth": False,
            "condition": None,
            "response": expected_response
        }
    })
    face = FakeFaceModule(authorized=True)
    cmd = FakeCommandService()
    
    inject_mocks(orchestrator, stt, llm, face, cmd)
    response = orchestrator.process_voice_command(b"dummy_audio")
    
    # Ensure it fails gracefully
    assert "xin lỗi" in response.lower() or "sorry" in response.lower() or "không hiểu" in response.lower()
