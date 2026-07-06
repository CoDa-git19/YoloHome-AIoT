from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol


class HardwareReceiver(Protocol):
    """
    Receiver interface for the Command Pattern.

    Any hardware gateway/module can be used as long as it implements:
        execute_command(command: dict[str, Any]) -> dict[str, Any] | bool

    In the real system, this receiver will usually be HardwareModule,
    MQTT gateway, or Adafruit IO gateway.
    """

    def execute_command(self, command: dict[str, Any]) -> dict[str, Any] | bool:
        ...


class Command(ABC):
    """
    Command interface.

    Each concrete command encapsulates one device action.
    CommandService only calls execute()/undo(), without knowing
    the low-level hardware payload details.
    """

    @abstractmethod
    def execute(self) -> bool:
        """Execute the command."""
        raise NotImplementedError

    @abstractmethod
    def undo(self) -> bool:
        """Undo the command if the operation is safe and supported."""
        raise NotImplementedError


class DeviceCommand(Command):
    """
    Base class for simple device-control commands.

    Subclasses only need to define:
        device
        action
        undo_action
    """

    device: str
    action: str
    undo_action: str | None = None

    def __init__(self, hardware: HardwareReceiver, room: str) -> None:
        self.hardware = hardware
        self.room = room

    def _build_payload(self, action: str) -> dict[str, Any]:
        return {
            "device": self.device,
            "action": action,
            "room": self.room,
        }

    def _send(self, action: str) -> bool:
        payload = self._build_payload(action)
        result = self.hardware.execute_command(payload)

        if isinstance(result, dict):
            return result.get("status") == "success"

        return bool(result)

    def execute(self) -> bool:
        return self._send(self.action)

    def undo(self) -> bool:
        if self.undo_action is None:
            return False

        return self._send(self.undo_action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "action": self.action,
            "room": self.room,
            "undo_action": self.undo_action,
        }


class TurnOnLightCommand(DeviceCommand):
    """Turn on a light in a specific room."""

    device = "light"
    action = "turn_on"
    undo_action = "turn_off"


class TurnOffLightCommand(DeviceCommand):
    """Turn off a light in a specific room."""

    device = "light"
    action = "turn_off"
    undo_action = "turn_on"


class TurnOnFanCommand(DeviceCommand):
    """Turn on a fan in a specific room."""

    device = "fan"
    action = "turn_on"
    undo_action = "turn_off"


class TurnOffFanCommand(DeviceCommand):
    """Turn off a fan in a specific room."""

    device = "fan"
    action = "turn_off"
    undo_action = "turn_on"


class OpenDoorCommand(DeviceCommand):
    """
    Open the main door.

    This command must only be executed after Face Auth passes.
    CommandService should not execute this command directly when
    next_step == "auth_required".
    """

    device = "door"
    action = "open"
    undo_action = "close"


class CloseDoorCommand(DeviceCommand):
    """
    Close the main door.

    Undo is intentionally disabled because undoing close would open the door,
    and opening the door is security-sensitive. It must go through Face Auth.
    """

    device = "door"
    action = "close"
    undo_action = None

    def undo(self) -> bool:
        return False


class GetStatusCommand(Command):
    """
    Query device status.

    This command represents get_status uniformly with device-control commands.
    It has no meaningful undo operation.
    """

    def __init__(self, hardware: HardwareReceiver, device: str, room: str) -> None:
        self.hardware = hardware
        self.device = device
        self.room = room
        self.action = "get_status"

    def execute(self) -> bool:
        payload = {
            "device": self.device,
            "action": self.action,
            "room": self.room,
        }

        result = self.hardware.execute_command(payload)

        if isinstance(result, dict):
            return result.get("status") == "success"

        return bool(result)

    def undo(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "action": self.action,
            "room": self.room,
            "undo_action": None,
        }


def create_device_command(
    command_data: dict[str, Any],
    hardware: HardwareReceiver,
) -> Command:
    """
    Mapping helper for mapping validated JSON command data to a concrete Command object.
    This helper keeps CommandService cleaner.
    Expected command_data:
        {
            "device": "light" | "fan" | "door",
            "action": "turn_on" | "turn_off" | "open" | "close" | "get_status",
            "room": "living_room" | "bedroom" | "main_door"
        }
    """
    device = command_data.get("device")
    action = command_data.get("action")
    room = command_data.get("room")

    if not device or not action or not room:
        raise ValueError(f"Missing command fields: {command_data}")

    if action == "get_status":
        return GetStatusCommand(
            hardware=hardware,
            device=device,
            room=room,
        )

    if device == "light" and action == "turn_on":
        return TurnOnLightCommand(hardware, room)

    if device == "light" and action == "turn_off":
        return TurnOffLightCommand(hardware, room)

    if device == "fan" and action == "turn_on":
        return TurnOnFanCommand(hardware, room)

    if device == "fan" and action == "turn_off":
        return TurnOffFanCommand(hardware, room)

    if device == "door" and action == "open":
        return OpenDoorCommand(hardware, room)

    if device == "door" and action == "close":
        return CloseDoorCommand(hardware, room)

    raise ValueError(f"Unsupported command: {device}.{action} in {room}")