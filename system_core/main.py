import time
import threading
from config import settings

# --- IMPORT MODULES AND SERVICES ---

# Import Concrete Modules
# from modules.hardware_gateway.hardware_module import HardwareModule
# from modules.speech_recognition.stt_module import STTModule
# from modules.face_recognition.face_module import FaceModule

from modules.llm_integration.llm_strategy import GeminiLLMStrategy, MockLLMStrategy

# Import Services
from services.command_service import CommandService
from services.rule_service import RuleService
# from services.logging_service import LoggingService

######################################
# MAIN ORCHESTRATOR (SYSTEM GATEWAY)
######################################

class MainOrchestrator:
    def __init__(self):
        print("[System] Initializing YoloHome-AIoT Gateway...")
        
        # Environment configuration is centralized in config.settings
        self.use_mock_llm = settings.USE_MOCK_LLM
        self.gemini_api_key = settings.GEMINI_API_KEY
        self.gemini_model = settings.GEMINI_MODEL
        self.face_threshold = settings.FACE_AUTH_THRESHOLD
        
        print(f"[Config] USE_MOCK_LLM = {self.use_mock_llm}")
        print(f"[Config] FACE_AUTH_THRESHOLD = {self.face_threshold}")
        
        # --- INITIALIZE MAIN MODULES ---
        # Hardware & STT
        self.hardware_module = None # HardwareModule(username=os.getenv("ADAFRUIT_IO_USERNAME"), key=os.getenv("ADAFRUIT_IO_KEY"))
        self.stt_engine = None      # STTModule(model_weights="base")
        
        if self.use_mock_llm:
            print("[System] Running LLM in MOCK mode (No API calls).")
            self.llm_engine = MockLLMStrategy()
        else:
            print(f"[System] Running LLM in REAL mode using {self.gemini_model}.")
            self.llm_engine = GeminiLLMStrategy(use_mock=False)
            
        self.face_module = None     # FaceModule(threshold=self.face_threshold)
        
        # --- INITIALIZE SERVICES ---
        self.logging_service = None # LoggingService(db_url=os.getenv("DATABASE_URL"))
        self.rule_service = RuleService()
        self.command_service = CommandService(
            rule_service=self.rule_service,
            hardware_module=self.hardware_module,
            use_mock=self.use_mock_llm,
        )
        
        # --- ASSEMBLE OBSERVER PATTERN ---
        if self.hardware_module:
            self.hardware_module.attach(self.rule_service)
            if self.logging_service:
                 self.hardware_module.attach(self.logging_service)
            
        self.latest_sensor_data = {}
            
        print("[System] Initialization complete!")

    def start_sensor_loop(self):
        """Continuously read sensor data"""
        print("[System] Starting background sensor monitoring thread...")
        while True:
            if self.hardware_module:
                self.latest_sensor_data = self.hardware_module.read_sensors()
            else:
                self.latest_sensor_data = {"temperature": 26.5, "humidity": 55.0}
                
            time.sleep(5)

    def process_voice_command(self, audio_data: bytes) -> str:
        """Main Pipeline: End-to-end voice processing"""
        print("\n" + "="*50)
        print("[Pipeline] Processing new voice command...")
        
        # STT Phase
        print("[STT] Converting speech to text...")
        if not self.stt_engine:
            return "Speech-to-text is not configured. Cannot process voice command."
        transcript = self.stt_engine.transcribe(audio_data)
        
        # LLM Phase
        print("[LLM] Analyzing intent with current sensor context...")
        if self.llm_engine:
            llm_result = self.llm_engine.parse_and_validate(transcript, self.latest_sensor_data)
        else:
            llm_result = {
                "ok": True,
                "command": {
                    "action": "open",
                    "device": "door",
                    "room": "main_door",
                    "face_auth": True,
                },
            }
        
        if not llm_result.get("ok"):
            print("   => Error: Invalid command.")
            return "Sorry, I couldn't understand that command."
            
        command_data = llm_result.get("command") or {}
        next_step = llm_result.get("next_step", "stop")
        if next_step == "create_rule":
            rule_id = self.rule_service.create_rule(command_id=None, command=command_data)
            return (
                f"Automation rule created successfully (ID: {rule_id})."
                if rule_id > 0
                else "Sorry, I couldn't create that automation rule."
            )
        if next_step not in {"execute", "auth_required"}:
            return command_data.get("response") or "Sorry, I couldn't understand that command."
        
        # FaceID Phase (Conditional)
        if command_data.get("face_auth", False):
            print("[FaceID] Security clearance required. Activating camera...")
            if not self.face_module:
                            return "Access denied. Face verification is required but not configured."
            face_result = self.face_module.verify_face()   
         
            if not face_result.get("authorized"):
                print("   => DENIED: Face not recognized!")
                return "Access denied. Identity verification failed."
            print(f"   => Verification successful! Welcome, {face_result.get('person_name')}.")
        else:
            print("[FaceID] No security clearance required. Skipping face scan.")

        # Execute Phase
        print("[Execute] Creating and executing IoT Command...")
        if self.command_service:
            self.command_service.execute_authorized_command(command_data)
        else:
            print(f"   => [Mock] Sending command to hardware/Adafruit: {command_data}")
            
        print("="*50)
        return "Command executed successfully!"

if __name__ == "__main__":
    orchestrator = MainOrchestrator()

    sensor_thread = threading.Thread(target=orchestrator.start_sensor_loop, daemon=True)
    sensor_thread.start()

    time.sleep(1)
    print("\n--- TEMPORARY CONSOLE CHAT INTERFACE ---")
    while True:
        user_input = input("Press Enter to simulate voice input (or type 'exit' to quit): ")
        if user_input.lower() == 'exit':
            break

        dummy_audio_bytes = b"dummy_audio_data"
        response = orchestrator.process_voice_command(dummy_audio_bytes)
        print(f"[Bot Reply]: {response}")
