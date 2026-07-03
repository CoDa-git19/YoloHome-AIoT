from abc import ABC, abstractmethod
from typing import Dict, Any

# --- INTERFACES ---
class STTStrategy(ABC):
    @abstractmethod
    def transcribe(self, audio_data: bytes) -> str:
        """Convert speech to text"""
        pass

class LLMStrategy(ABC):
    @abstractmethod
    def parse_and_validate(self, transcript: str) -> Dict[str, Any]:
        """Parse text into a standard JSON string"""
        pass
    
# --- IMPLEMENTATIONS ---
# class PhoWhisperStrategy(STTStrategy)
    # # Example
    # def transcribe(self, audio_data: bytes) -> str:
    #         # Code to load the PhoWhisper model and process audio goes here
    #         print("Processing audio...")
    #         return "Turn on the living room lights"
    
# class GeminiStrategy(LLMStrategy)
    # # Example
    # def parse_command(self, text: str) -> Dict[str, Any]:
    #     # Code to call the Gemini API is here
    #     print(f"Analyzing the sentence: {text}")
    #     return {"intent": "control_device", "action": "turn_on", "device": "light"}