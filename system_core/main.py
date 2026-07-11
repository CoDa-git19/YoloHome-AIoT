import os
import time
import threading
from dotenv import load_dotenv

# --- IMPORT MODULES AND SERVICES ---

# Import Interfaces (Design Patterns)
from system_core.strategies import STTStrategy, LLMStrategy
from system_core.commands import Command
from system_core.observers import Observer, Subject

# Import Concrete Modules
# from modules.hardware_gateway.hardware_module import HardwareModule
# from modules.speech_recognition.stt_module import STTModule
# from modules.llm_integration.llm_module import LLMModule
# from modules.face_recognition.face_module import FaceModule

# Import Services (Integration Layer)
# from services.command_service import CommandService
# from services.rule_service import RuleService
# from services.logging_service import LoggingService

######################################
# MAIN ORCHESTRATOR (SYSTEM GATEWAY)
######################################

class MainOrchestrator:
    def __init__(self):
        print("[System] Initializing YoloHome-AIoT Gateway...")
        
        load_dotenv()  # Load environment variables from .env file
        
        self.use_mock_llm = os.getenv("USE_MOCK_LLM", "true").lower() == "true"
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.face_threshold = float(os.getenv("FACE_AUTH_THRESHOLD", "0.80"))
        
        print(f"[Config] USE_MOCK_LLM = {self.use_mock_llm}")
        print(f"[Config] FACE_AUTH_THRESHOLD = {self.face_threshold}")
        
        # --- INITIALIZE MAIN MODULES ---
        # Tạm thời gán None hoặc Mock class. Sau này sẽ thay bằng class thật
        self.hardware_module = None # HardwareModule(username=os.getenv("ADAFRUIT_IO_USERNAME"), key=os.getenv("ADAFRUIT_IO_KEY"))
        self.stt_engine = None      # STTModule(model_weights="base")
        if self.use_mock_llm:
            print("[System] Running LLM in MOCK mode (No API calls).")
            self.llm_engine = None  # MockLLMModule()
        else:
            print(f"[System] Running LLM in REAL mode using {self.gemini_model}.")
            self.llm_engine = None  # LLMModule(api_key=self.gemini_api_key, model=self.gemini_model)
        self.face_module = None     # FaceModule(threshold=self.face_threshold)
        
        # --- INITIALIZE SERVICES ---
        self.logging_service = None # LoggingService(db_url=os.getenv("DATABASE_URL"))
        self.rule_service = None    # RuleService()
        self.command_service = None # CommandService(self.hardware_module, self.logging_service)
        
        # --- CONNECT OBSERVER PATTERN ---
        # Hardware automatically notifies RuleService and LoggingService upon receiving new sensor data
        if self.hardware_module:
            self.hardware_module.attach(self.rule_service)
            self.hardware_module.attach(self.logging_service)
        
        # Store the latest sensor data to feed into the LLM
        self.latest_sensor_data = {}
        
        print("[System] Initialization complete!")

    def start_sensor_loop(self):
        """a) Background thread: Continuously read sensor data"""
        print("[System] Starting background sensor monitoring thread...")
        while True:
            if self.hardware_module:
            # read_sensors() will internally call self.notify() to trigger Observers (including Adafruit IO publish)
                self.latest_sensor_data = self.hardware_module.read_sensors()
            else:
                # Mock data if hardware is not yet integrated
                self.latest_sensor_data = {"temperature": 26.5, "humidity": 55.0}
            
            time.sleep(5) # Polling interval: 5 seconds

    def process_voice_command(self, audio_data: bytes) -> str:
        """Main Pipeline: End-to-end voice processing"""
        print("\n" + "="*50)
        print("[Pipeline] Processing new voice command...")
        
        # Gọi STT -> Lấy transcript
        print("[1. STT] Converting speech to text...")
        transcript = self.stt_engine.transcribe(audio_data) if self.stt_engine else "open the door"
        print(f"   => Transcript: '{transcript}'")
        
        # Gọi LLM để hiểu ý định và check xem có cần xác thực FaceID không
        print("[2. LLM] Analyzing intent with current sensor context...")
        if self.llm_engine:
            llm_result = self.llm_engine.parse_and_validate(transcript, self.latest_sensor_data) 
        else:
            llm_result = {
            "ok": True, 
            "command": {"action": "open", "device": "door", "room": "main_door", "face_auth": True}
        }
        
        if not llm_result.get("ok"):
            print("   => Error: Invalid command.")
            return "Sorry, I couldn't understand that command."
            
        command_data = llm_result.get("command", {})
        
        # Kiểm tra xem lệnh có cần xác thực khuôn mặt không (VD: Mở cửa)
        if command_data.get("face_auth", False):
            print("[3. FaceID] Security clearance required. Activating camera...")
            face_result = self.face_module.verify_face() if self.face_module else {"authorized": True, "person_name": "Admin"}
            
            if not face_result.get("authorized"):
                print("   => Error: Invalid face.")
                return "Access denied. Face does not match."
            print(f"   => Verification successful! Welcome, {face_result.get('person_name')}.")
        else:
            print("[3. FaceID] No security clearance required. Skipping face scan.")

        # Thực thi lệnh: Gọi Command Pattern
        print("[4. Execute] Creating and executing IoT Command...")
        if self.command_service:
            # Tạo 1 ID ngẫu nhiên cho log
            command_id = int(time.time())
            self.command_service.execute_parsed_command(command_data, command_id)
        else:
            print(f"   => [Mock] Sending command to hardware/Adafruit: {command_data}")
            
        # g) Trả kết quả về cho giao diện Chat
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
        print(f"[Bot response]: {response}")
