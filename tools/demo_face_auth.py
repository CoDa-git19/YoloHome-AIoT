"""
Smoke test: Demo tính năng nhận diện khuôn mặt và liveness detection thông qua AuthService.

Cách chạy:
    python -m tools.demo_face_auth
"""

import sys
import logging

# Fix cho Windows Terminal không in được tiếng Việt
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from services.auth_service import AuthService
from services.command_service import CommandService


# Thiết lập log để xem chi tiết
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("demo_face")

def main():
    print("="*60)
    print("   YoloHome AIoT - Face Authentication Demo")
    print("="*60)
    
    print("\nKhởi tạo AuthService với màn hình hiển thị (display=True)...")
    
    # Tạo command service giả
    class DummyCommandService(CommandService):
        def _get_llm_module(self):
            return None
        
        def execute(self, command_data):
            print(f">>> [HỆ THỐNG THỰC THI THÀNH CÔNG] {command_data['action']} {command_data['device']} <<<")
            return True
    
    try:
        # Khởi tạo AuthService bật tính năng nhận diện và liveness, hiển thị cửa sổ webcam
        auth_service = AuthService(
            command_service=DummyCommandService(),
            camera_index=0,
            timeout_seconds=15.0, # Cho thời gian dài hơn một chút để test
            required_frames=3,
            use_liveness=True,
            required_blinks=1,
            display=True,
            window_title="YoloHome - Demo Face Auth"
        )
        
        # Giả lập một command cần xác thực
        command_data = {
            "action": "open",
            "device": "main_door",
            "room": "living_room",
            "face_auth": True
        }
        
        print(f"\n[DEMO] Giả lập LLM trả về lệnh cần xác thực: {command_data}")
        print("[DEMO] Chú ý: Hãy chớp mắt 1 lần khi ở trước camera (nếu đã bật liveness).")
        print("[DEMO] Nhấn phím 'q' trên cửa sổ webcam để thoát sớm nếu muốn.\n")
        
        # Chạy xác thực
        result = auth_service.authenticate_and_execute(command_data)
        
        print("\n" + "="*60)
        print(" KẾT QUẢ XÁC THỰC ")
        print("="*60)
        
        if result.get("authorized"):
            print(f"[THÀNH CÔNG] Đã xác thực thành công!")
            print(f"Người thực hiện: {result.get('person_name')} (Confidence: {result.get('confidence')})")
            print(f"Lệnh đã thực thi: {result.get('executed')}")
        else:
            print(f"[THẤT BẠI] Xác thực không thành công hoặc timeout.")
            print(f"Lý do: {result.get('response', 'Không rõ')}")

            
    except ImportError as e:
        logger.error(f"Lỗi thiếu thư viện: {e}")
        print("\nVui lòng cài đặt đầy đủ thư viện: pip install -r requirements.txt")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Lỗi không xác định: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
