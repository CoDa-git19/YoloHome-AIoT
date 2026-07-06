from __future__ import annotations

from typing import Any

import services.command_service as command_module
from services.command_service import CommandService
from system_core.commands import (
    CloseDoorCommand,
    GenericDeviceCommand,
    GetStatusCommand,
    OpenDoorCommand,
    create_device_command,
)
from system_core.strategies import LLMStrategy
import pytest

class RecordingHardware:
    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.commands: list[dict[str, Any]] = []

    def execute_command(self, command: dict[str, Any]) -> dict[str, Any]:
        self.commands.append(dict(command))
        return {
            "status": self.status,
            "command": command,
        }


class FixedLLMStrategy(LLMStrategy):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def parse_and_validate(
        self,
        transcript: str,
        sensor_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "transcript": transcript,
                "sensor_data": sensor_data,
            }
        )
        return self.result


class DummyRuleService:
    def create_rule(
        self,
        command_id: int | None,
        command: dict[str, Any],
    ) -> int:
        return 1


def _command(
    *,
    intent: str = "control_device",
    action: str = "turn_on",
    device: str = "light",
    room: str = "living_room",
    face_auth: bool = False,
    response: str = "OK",
) -> dict[str, Any]:
    return {
        "intent": intent,
        "action": action,
        "device": device,
        "room": room,
        "face_auth": face_auth,
        "condition": None,
        "response": response,
    }


def _llm_result(
    *,
    command: dict[str, Any],
    next_step: str,
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "transcript": "test transcript",
        "command": command,
        "validation": {
            "passed": ok,
            "code": "valid_command" if ok else "invalid_command",
            "message": "OK" if ok else "Invalid command.",
        },
        "next_step": next_step,
        "latency_ms": 1,
        "log_result": None,
        "error": None,
    }


def _disable_command_db_logging(monkeypatch):
    logged_commands: list[dict[str, Any]] = []
    updated_commands: list[dict[str, Any]] = []

    def fake_log_command(
        transcript: str,
        json_cmd: dict[str, Any],
        result: str,
        validation_status: str | None = None,
        execution_status: str | None = None,
        latency_ms: int | None = None,
        error_message: str | None = None,
    ) -> int:
        logged_commands.append(
            {
                "transcript": transcript,
                "json_cmd": json_cmd,
                "result": result,
                "validation_status": validation_status,
                "execution_status": execution_status,
                "latency_ms": latency_ms,
                "error_message": error_message,
            }
        )
        return 1

    def fake_update_command_result(
        command_id: int,
        result: str,
        execution_status: str | None = None,
        error_message: str | None = None,
    ) -> None:
        updated_commands.append(
            {
                "command_id": command_id,
                "result": result,
                "execution_status": execution_status,
                "error_message": error_message,
            }
        )

    monkeypatch.setattr(command_module, "log_command", fake_log_command)
    monkeypatch.setattr(command_module, "update_command_result", fake_update_command_result)
    monkeypatch.setattr(command_module, "log_error", lambda module, message: None)

    return logged_commands, updated_commands


def test_command_service_uses_injected_llm_strategy(monkeypatch):
    _disable_command_db_logging(monkeypatch)

    command = _command(
        action="turn_on",
        device="light",
        room="living_room",
        response="Đã bật đèn phòng khách.",
    )
    strategy = FixedLLMStrategy(
        _llm_result(
            command=command,
            next_step="execute",
        )
    )
    hardware = RecordingHardware()

    service = CommandService(
        rule_service=DummyRuleService(),
        hardware_module=hardware,
        llm_strategy=strategy,
    )

    result = service.handle_transcript(
        "bật đèn phòng khách",
        sensor_data={"temperature": 30},
    )

    assert strategy.calls == [
        {
            "transcript": "bật đèn phòng khách",
            "sensor_data": {"temperature": 30},
        }
    ]
    assert result["ok"] is True
    assert result["execution_status"] == "success"
    assert hardware.commands == [
        {
            "device": "light",
            "action": "turn_on",
            "room": "living_room",
        }
    ]


def test_auth_required_does_not_execute_hardware(monkeypatch):
    _disable_command_db_logging(monkeypatch)

    command = _command(
        action="open",
        device="door",
        room="main_door",
        face_auth=True,
        response="Cần xác thực khuôn mặt trước khi mở cửa.",
    )
    strategy = FixedLLMStrategy(
        _llm_result(
            command=command,
            next_step="auth_required",
        )
    )
    hardware = RecordingHardware()

    service = CommandService(
        rule_service=DummyRuleService(),
        hardware_module=hardware,
        llm_strategy=strategy,
    )

    result = service.handle_transcript("mở cửa chính")

    assert result["ok"] is True
    assert result["next_step"] == "auth_required"
    assert result["execution_status"] == "waiting_auth"
    assert hardware.commands == []


def test_execute_authorized_command_executes_after_external_auth():
    hardware = RecordingHardware()
    service = CommandService(
        rule_service=DummyRuleService(),
        hardware_module=hardware,
        llm_strategy=FixedLLMStrategy(
            _llm_result(
                command=_command(),
                next_step="execute",
            )
        ),
    )

    success = service.execute_authorized_command(
        {
            "intent": "control_device",
            "action": "open",
            "device": "door",
            "room": "main_door",
            "face_auth": True,
            "condition": None,
            "response": "Đã mở cửa chính.",
        }
    )

    assert success is True
    assert hardware.commands == [
        {
            "device": "door",
            "action": "open",
            "room": "main_door",
        }
    ]


def test_create_device_command_execute_and_undo_light():
    hardware = RecordingHardware()

    command = create_device_command(
        {
            "device": "light",
            "action": "turn_on",
            "room": "living_room",
        },
        hardware,
    )

    assert isinstance(command, GenericDeviceCommand)
    assert command.execute() is True
    assert command.undo() is True
    assert hardware.commands == [
        {
            "device": "light",
            "action": "turn_on",
            "room": "living_room",
        },
        {
            "device": "light",
            "action": "turn_off",
            "room": "living_room",
        },
    ]


def test_create_device_command_open_door_has_close_undo():
    hardware = RecordingHardware()

    command = create_device_command(
        {
            "device": "door",
            "action": "open",
            "room": "main_door",
        },
        hardware,
    )

    assert isinstance(command, OpenDoorCommand)
    assert command.execute() is True
    assert command.undo() is True
    assert hardware.commands == [
        {
            "device": "door",
            "action": "open",
            "room": "main_door",
        },
        {
            "device": "door",
            "action": "close",
            "room": "main_door",
        },
    ]


def test_close_door_undo_is_disabled():
    hardware = RecordingHardware()

    command = create_device_command(
        {
            "device": "door",
            "action": "close",
            "room": "main_door",
        },
        hardware,
    )

    assert isinstance(command, CloseDoorCommand)
    assert command.execute() is True
    assert command.undo() is False
    assert hardware.commands == [
        {
            "device": "door",
            "action": "close",
            "room": "main_door",
        }
    ]


def test_get_status_command_has_no_undo():
    hardware = RecordingHardware()

    command = create_device_command(
        {
            "device": "fan",
            "action": "get_status",
            "room": "bedroom",
        },
        hardware,
    )

    assert isinstance(command, GetStatusCommand)
    assert command.execute() is True
    assert command.undo() is False
    assert hardware.commands == [
        {
            "device": "fan",
            "action": "get_status",
            "room": "bedroom",
        }
    ]


def test_undo_last_command_uses_command_history(monkeypatch):
    _disable_command_db_logging(monkeypatch)

    command = _command(
        action="turn_on",
        device="light",
        room="living_room",
    )
    strategy = FixedLLMStrategy(
        _llm_result(
            command=command,
            next_step="execute",
        )
    )
    hardware = RecordingHardware()

    service = CommandService(
        rule_service=DummyRuleService(),
        hardware_module=hardware,
        llm_strategy=strategy,
    )

    result = service.handle_transcript("bật đèn phòng khách")
    undo_success = service.undo_last_command()
    second_undo_success = service.undo_last_command()

    assert result["execution_status"] == "success"
    assert undo_success is True
    assert second_undo_success is False
    assert hardware.commands == [
        {
            "device": "light",
            "action": "turn_on",
            "room": "living_room",
        },
        {
            "device": "light",
            "action": "turn_off",
            "room": "living_room",
        },
    ]

def test_generic_device_command_supports_future_switch_device():
    hardware = RecordingHardware()

    command = create_device_command(
        {
            "device": "pump",
            "action": "turn_on",
            "room": "garden",
        },
        hardware,
    )

    assert isinstance(command, GenericDeviceCommand)
    assert command.execute() is True
    assert command.undo() is True
    assert command.to_dict() == {
        "device": "pump",
        "action": "turn_on",
        "room": "garden",
        "undo_action": "turn_off",
    }
    assert hardware.commands == [
        {
            "device": "pump",
            "action": "turn_on",
            "room": "garden",
        },
        {
            "device": "pump",
            "action": "turn_off",
            "room": "garden",
        },
    ]

def test_generic_device_command_supports_cover_open_close():
    hardware = RecordingHardware()

    command = create_device_command(
        {
            "device": "curtain",
            "action": "open",
            "room": "living_room",
        },
        hardware,
    )

    assert isinstance(command, GenericDeviceCommand)
    assert command.execute() is True
    assert command.undo() is True
    assert command.to_dict() == {
        "device": "curtain",
        "action": "open",
        "room": "living_room",
        "undo_action": "close",
    }
    assert hardware.commands == [
        {
            "device": "curtain",
            "action": "open",
            "room": "living_room",
        },
        {
            "device": "curtain",
            "action": "close",
            "room": "living_room",
        },
    ]

def test_entry_point_close_has_no_unsafe_undo():
    hardware = RecordingHardware()

    command = create_device_command(
        {
            "device": "garage_door",
            "action": "close",
            "room": "garage",
        },
        hardware,
    )

    assert isinstance(command, GenericDeviceCommand)
    assert command.execute() is True
    assert command.undo() is False
    assert command.to_dict() == {
        "device": "garage_door",
        "action": "close",
        "room": "garage",
        "undo_action": None,
    }
    assert hardware.commands == [
        {
            "device": "garage_door",
            "action": "close",
            "room": "garage",
        }
    ]

def test_door_commands_remain_special_commands():
    hardware = RecordingHardware()

    open_command = create_device_command(
        {
            "device": "door",
            "action": "open",
            "room": "main_door",
        },
        hardware,
    )
    close_command = create_device_command(
        {
            "device": "door",
            "action": "close",
            "room": "main_door",
        },
        hardware,
    )

    assert isinstance(open_command, OpenDoorCommand)
    assert isinstance(close_command, CloseDoorCommand)