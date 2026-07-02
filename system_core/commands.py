from abc import ABC, abstractmethod

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
#     def __init__(self, room: str):
#         self.room = room

#     def execute(self) -> bool:
#         print(f"[Hardware] The room light is on: {self.room}")
#         # Code to send Serial/MQTT signals to Yolo:Bit
#         return True

#     def undo(self) -> bool:
#         print(f"[Hardware] The room light is off (Undo): {self.room}")
#         # Code to send Serial/MQTT signals to Yolo:Bit
#         return True

# Invoker
class CommandInvoker:
    def __init__(self):
        self._history = []

    def execute_command(self, command: Command):
        """Execute the command and save it to history if successful"""
        if command.execute():
            self._history.append(command)
            # Later on, the database can be called here to log the 'success' status
            # print("[Database] Log saved successfully.")

    def undo_last_command(self):
        """Remove the last command from history and execute its undo() method"""
        if self._history:
            command = self._history.pop()
            command.undo()
            # Update DB to 'undo' status
            # print("[Database] Undo log saved.")
