import cv2
import time
from modules.face_recognition.face_module import SvmFaceRecognizer, capture_frame

def main():
    print("=== Khởi động kiểm tra Module Face Recognition ===")
    print("Đang tải model (có thể mất vài giây)...")
    try:
        recognizer = SvmFaceRecognizer()
        print("[Thành công] Đã tải model SvmFaceRecognizer!")
    except Exception as e:
        print(f"[Lỗi] Không thể tải model: {e}")
        print("Gợi ý: Hãy chắc chắn rằng bạn đã có thư mục 'models' ở root dự án và file 'face_model.pkl' ở trong đó.")
        return

    print("\nBật camera (Hãy nhìn vào camera và thử chớp mắt nhé)...")
    # Đặt require_blink=True để trải nghiệm thử tính năng liveness detection
    frame = capture_frame(require_blink=True)
    
    if frame is None:
        print("[Lỗi] Không thể kết nối với camera. Hãy kiểm tra lại webcam của bạn.")
        return

    print("[Thành công] Đã chụp được ảnh từ camera. Đang phân tích khuôn mặt...")
    
    # Đo thời gian nhận diện
    start_time = time.time()
    result = recognizer.recognize(frame)
    latency = time.time() - start_time
    
    print("\n=== KẾT QUẢ NHẬN DIỆN ===")
    print(f"- Tên người dùng: {result.get('person_name')}")
    print(f"- Độ tin cậy (Confidence): {result.get('confidence'):.2f}")
    print(f"- Thời gian xử lý: {latency:.2f} giây")
    
    # Hiển thị ảnh chụp (có vẽ khung nếu muốn)
    print("\nNhấn phím bất kỳ trên cửa sổ ảnh để đóng.")
    cv2.putText(frame, f"{result.get('person_name')} ({result.get('confidence'):.2f})", 
                (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.imshow("Test Face Module", frame)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
