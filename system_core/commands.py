from abc import ABC, abstractmethod
from modules.hardware_gateway.hardware_module import HardwareModule

# --- INTERFACES ---
class Command(ABC):
    @abstractmethod
    def execute(self) -> bool:
        """Execute the command, returning True if successful"""
        pass

    @abstractmethod
    def undo(self) -> bool:
        """Undo the command (e.g., if it's on, turn it off)"""
        pass

# --- IMPLEMENTATIONS (Map the LLM's JSON output to these classes) ---
# class TurnOnLightCommand(Command):
#     def __init__(self, hardware_module: HardwareModule, room: str):
#         self.room = room

#     def execute(self) -> bool:
#         print(f"[Hardware] The room light is on: {self.room}")
#         # Code to send Serial/MQTT signals to Yolo:Bit
#         return True

#     def undo(self) -> bool:
#         print(f"[Hardware] The room light is off (Undo): {self.room}")
#         # Code to send Serial/MQTT signals to Yolo:Bit
#         return True


class TurnOnLightCommand(Command):
    def __init__(self, hardware_module: HardwareModule, room: str):
        # Inject the Receiver (HardwareModule) via Dependency Injection
        self.hardware = hardware_module
        self.room = room

    def execute(self) -> bool:
        print(f"[Command] Sending command to turn on the room light: {self.room}")
        # Call the Receiver to perform the work (e.g., send Serial/MQTT signals to Yolo:Bit)
        payload = {"device": "light", "action": "turn_on", "room": self.room}
        result = self.hardware.execute_command(payload)
        return result.get("status") == "success"

    def undo(self) -> bool:
        print(f"[Command] Undo: Sending command to turn off the room light: {self.room}")
        payload = {"device": "light", "action": "turn_off", "room": self.room}
        result = self.hardware.execute_command(payload)
        return result.get("status") == "success"

class TurnOffFanCommand(Command):
    def __init__(self, hardware_module: HardwareModule, room: str):
        self.hardware = hardware_module
        self.room = room

    def execute(self) -> bool:
        print(f"[Command] Sending command to turn off the room fan: {self.room}")
        payload = {"device": "fan", "action": "turn_off", "room": self.room}
        result = self.hardware.execute_command(payload)
        return result.get("status") == "success"

    def undo(self) -> bool:
        print(f"[Command] Undo: Sending command to turn on the room fan: {self.room}")
        payload = {"device": "fan", "action": "turn_on", "room": self.room}
        result = self.hardware.execute_command(payload)
        return result.get("status") == "success"
