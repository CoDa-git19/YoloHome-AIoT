from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol
CommandPayload = dict[str, Any]
from config.capabilities import is_special_command, resolve_action_capability

class HardwareReceiver(Protocol):
    """
    Receiver interface for the Command Pattern.

    Any hardware gateway/module can be used as long as it implements:
        execute_command(command: dict[str, Any]) -> dict[str, Any] | bool

    In the real system, this receiver will usually be HardwareModule,
    MQTT gateway, or Adafruit IO gateway.
    """

    def execute_command(self, command: CommandPayload) -> CommandPayload | bool:
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

    def _build_payload(self, action: str) -> CommandPayload:
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

    def to_dict(self) -> CommandPayload:
        return {
            "device": self.device,
            "action": self.action,
            "room": self.room,
            "undo_action": self.undo_action,
        }


class GenericDeviceCommand(DeviceCommand):
    """
    Generic command for validated device actions.

    Action behavior such as undo_action and safety level is resolved from
    config/device_capabilities.json instead of being hardcoded in Python.
    """

    def __init__(
        self,
        hardware: HardwareReceiver,
        device: str,
        action: str,
        room: str,
        undo_action: str | None = None,
    ) -> None:
        self.device = device
        self.action = action
        self.undo_action = undo_action
        super().__init__(hardware=hardware, room=room)


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
    undo_action: str | None = None

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
        return False

    def to_dict(self) -> CommandPayload:
        return {
            "device": self.device,
            "action": self.action,
            "room": self.room,
            "undo_action": self.undo_action,
        }


SPECIAL_COMMANDS: dict[tuple[str, str], type[DeviceCommand]] = {
    ("door", "open"): OpenDoorCommand,
    ("door", "close"): CloseDoorCommand,
}


def create_device_command(
    command_data: CommandPayload,
    hardware: HardwareReceiver,
) -> Command:
    """
    Convert validated JSON command data to a Command object.

    Factory policy:
    - get_status uses GetStatusCommand.
    - special commands use dedicated command classes.
    - generic actions are resolved from config/device_capabilities.json.

    The factory assumes command_data has already passed Validator.
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

    special_command_cls = SPECIAL_COMMANDS.get((device, action))
    if special_command_cls is not None:
        return special_command_cls(hardware, room)

    if is_special_command(device=device, action=action):
        raise ValueError(f"Special command not implemented: {device}.{action}")

    capability = resolve_action_capability(device=device, action=action)

    if not capability.generic:
        raise ValueError(
            f"Action is not generic: {device}.{action} "
            f"for device_type={capability.device_type}"
        )

    return GenericDeviceCommand(
        hardware=hardware,
        device=device,
        action=action,
        room=room,
        undo_action=capability.undo_action,
    )