"""
Công cụ kiểm tra thủ công Face module.

ĐÂY KHÔNG PHẢI TEST TỰ ĐỘNG. File cần webcam thật, cần models/face_model.pkl,
và cần một người ngồi trước máy chớp mắt. Không có assert nào.

Chạy:
    python -m tests.modules.face.test_face

Test tự động tương ứng nằm ở test_face_module_contract.py cùng thư mục -
file đó chạy offline, không cần camera hay dlib.

VÌ SAO IMPORT NẶNG NẰM TRONG HÀM
--------------------------------
Tên file bắt đầu bằng "test_" nên pytest sẽ IMPORT nó mỗi lần chạy suite,
dù không chạy main(). Nếu `import cv2` nằm ở đầu file thì mọi máy chưa cài
OpenCV sẽ đỏ toàn bộ test suite ngay ở khâu collection - kể cả những bạn
không đụng gì tới phần nhận diện khuôn mặt.

Đưa import vào trong main() thì pytest import file thành công, thấy không có
hàm test_* nào, thu thập 0 test rồi đi tiếp. Không cần conftest, không cần
đổi tên, không cần marker.
"""

from __future__ import annotations

import time

from config.settings import FACE_AUTH_THRESHOLD
from modules.face_recognition.face_module import SvmFaceRecognizer, capture_frame


def main() -> None:
    import cv2

    print("=== Kiểm tra Module Face Recognition ===")
    print("Đang tải model (có thể mất vài giây)...")

    try:
        recognizer = SvmFaceRecognizer()
        print("[Thành công] Đã tải SvmFaceRecognizer.")
    except FileNotFoundError as exc:
        print(f"[Lỗi] {exc}")
        return
    except Exception as exc:
        print(f"[Lỗi] Không thể tải model: {exc}")
        return

    print("\nBật camera - hãy nhìn vào camera và chớp mắt...")

    # cap=None: công cụ này TỰ mở và TỰ đóng camera.
    # Trong hệ thống thật, MainOrchestrator sở hữu VideoCapture và truyền
    # xuống qua tham số cap (Contract B) - không đi đường này.
    frame = capture_frame(require_blink=True)

    if frame is None:
        print("[Lỗi] Không lấy được khung hình.")
        print("Nguyên nhân thường gặp: webcam đang bị ứng dụng khác chiếm,")
        print("không phát hiện chớp mắt trong 15 giây, hoặc phòng quá tối.")
        return

    print("[Thành công] Đã chụp được ảnh. Đang phân tích...")

    start_time = time.time()
    result = recognizer.recognize(frame)
    latency = time.time() - start_time

    person_name = result.get("person_name")
    confidence = float(result.get("confidence") or 0.0)

    # Tái hiện đúng luật quyết định của server (Contract B - Step 2).
    # In ra ở đây để công cụ thể hiện được chính cái nó cần chứng minh:
    # NGƯỠNG LÀ QUYẾT ĐỊNH CỦA SERVER, không phải của face module.
    authorized = person_name is not None and confidence >= FACE_AUTH_THRESHOLD

    print("\n=== KẾT QUẢ ===")
    print(f"- person_name       : {person_name}")
    print(f"- confidence        : {confidence:.4f}")
    print(f"- ngưỡng server     : {FACE_AUTH_THRESHOLD}")
    print(f"- thời gian xử lý   : {latency:.2f}s")
    print(f"- KẾT LUẬN          : {'CHO PHÉP' if authorized else 'TỪ CHỐI'}")

    if person_name is None and confidence > 0:
        print("\n(person_name=None kèm confidence > 0 nghĩa là model nhận ra")
        print(" ĐÂY LÀ NGƯỜI LẠ với độ tin cậy đó - đúng như thiết kế.)")

    label = f"{person_name or 'DENIED'} ({confidence:.2f})"
    color = (0, 255, 0) if authorized else (0, 0, 255)
    cv2.putText(
        frame, label, (50, 50),
        cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2,
    )

    print("\nNhấn phím bất kỳ trên cửa sổ ảnh để đóng.")
    cv2.imshow("Test Face Module", frame)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()