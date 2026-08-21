"""
Face recognition module.

OWNER: Face module.

Vai trò trong pipeline: xác thực khuôn mặt cho các hành động nhạy cảm
(hiện tại là door.open). Module này CHỈ nhận diện khuôn mặt và trả về
độ tin cậy - nó KHÔNG quyết định cho phép hay không.

Quyết định cho phép (so sánh với FACE_AUTH_THRESHOLD) nằm ở
services/auth_service.py, vì ngưỡng là cấu hình phía server.

Xem hợp đồng đầy đủ: docs/Integration-Contracts.md (Contract B).

BA RÀNG BUỘC KIẾN TRÚC CỦA FILE NÀY
-----------------------------------
1. PHỤ THUỘC NẶNG ĐƯỢC IMPORT BÊN TRONG HÀM.
   cv2 / face_recognition (dlib) / numpy KHÔNG được import ở đầu file.
   FaceRecognizer, MockFaceRecognizer và SvmFaceRecognizer._as_identity()
   đều không cần chúng, nhưng lại được test suite dùng - mà test suite của
   nhóm phải chạy offline trên máy không cài dlib.

2. CAMERA ĐƯỢC MỞ THEO YÊU CẦU, KHÔNG GIỮ SẴN.
   MainOrchestrator gắn capture_frame() vào frame_capturer và gọi mỗi lần cần
   xác thực; camera mở ra rồi đóng lại ngay trong một lần quét. Nhờ vậy webcam
   rảnh khi hệ thống không quét - OBS, Zoom hay phần mềm quay màn hình vẫn
   dùng được trong lúc demo.
   Tham số `cap` cho phép người gọi tự sở hữu VideoCapture (test, hoặc ngữ
   cảnh cần tái sử dụng kết nối). Khi truyền cap, NGƯỜI GỌI chịu trách nhiệm
   đóng nó.

3. QUY TẮC QUYẾT ĐỊNH PHẢI KHỚP VỚI NOTEBOOK HUẤN LUYỆN.
   Notebook đánh giá bằng argmax(predict_proba). recognize() cũng phải dùng
   đúng cách đó, nếu không con số accuracy trong evaluation_metrics.txt không
   mô tả hành vi đang chạy. Xem chú thích trong recognize().
"""

from __future__ import annotations

import math
import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from config.settings import MODELS_DIR


# =============================================================================
# Tham số liveness
# =============================================================================

# Ngưỡng Eye Aspect Ratio để coi là "mắt đang nhắm".
# Cao quá -> mắt hơi hé cũng tính là nhắm (dễ bị qua mặt).
# Thấp quá -> người đeo kính hoặc mắt nhỏ không bao giờ chớp "đủ sâu".
# Chỉnh theo ánh sáng phòng và góc camera thật; xem docs/Face-Recognition.md.
EAR_THRESHOLD = 0.21

DEFAULT_TIMEOUT_SECONDS = 15

# Camera hỏng / bị rút giữa chừng: cap.read() trả False liên tục. Không đếm
# thì vòng lặp sẽ quay hết timeout ở 100% CPU mà không hiện gì.
MAX_CONSECUTIVE_READ_FAILURES = 30


# =============================================================================
# Interface
# =============================================================================

class FaceRecognizer(ABC):
    """
    Interface mà auth_service phụ thuộc vào.

    Concrete implementation:
    - SvmFaceRecognizer   (dlib embeddings + SVM classifier, models/face_model.pkl)
    - MockFaceRecognizer  (dùng cho test/demo, không cần camera)
    """

    @abstractmethod
    def recognize(self, frame: Any) -> dict[str, Any]:
        """
        Nhận diện khuôn mặt trong một khung hình.

        Args:
            frame: Ảnh/khung hình (ví dụ numpy array từ OpenCV). Có thể là None
                khi chưa lấy được frame - implementation nên coi đó là "no_face".

        Returns:
            {
                "person_name": str | None,   # None nếu không nhận ra NGƯỜI NHÀ
                "confidence": float,          # trong [0.0, 1.0]
            }

        LƯU Ý: KHÔNG tự quyết định authorized ở đây. Trả confidence, để
        auth_service so với FACE_AUTH_THRESHOLD.
        """
        raise NotImplementedError


class MockFaceRecognizer(FaceRecognizer):
    """
    Recognizer giả cho test/demo offline. Không cần camera hay model.

    CẢNH BÁO AN NINH: mặc định LUÔN nhận ra "member_1" với confidence 0.95,
    nghĩa là BẤT KỲ AI cũng qua được xác thực. Bật USE_MOCK_FACE=true chỉ để
    kiểm tra đường ống khi chưa có face_model.pkl - check_config() cảnh báo mỗi
    lần khởi động.

    Cho phép ép sẵn kết quả để test cả nhánh authorized lẫn denied:
        MockFaceRecognizer("member_1", 0.91)   -> qua ngưỡng 0.80
        MockFaceRecognizer(None, 0.0)           -> no_face
    """

    def __init__(
        self,
        person_name: str | None = "member_1",
        confidence: float = 0.95,
    ) -> None:
        self.person_name = person_name
        self.confidence = confidence

    def recognize(self, frame: Any = None) -> dict[str, Any]:
        return {
            "person_name": self.person_name,
            "confidence": self.confidence,
        }


# =============================================================================
# SVM recognizer (dlib 128-D embedding + SVM classifier)
# =============================================================================

class SvmFaceRecognizer(FaceRecognizer):
    """
    Dùng face_recognition để detect và trích embedding 128 chiều, sau đó dùng
    model SVM đã train (models/face_model.pkl) để phân loại.
    """

    # Nhãn của các class "không phải người nhà" trong dataset huấn luyện.
    #
    # KHÔNG BAO GIỜ được trả các nhãn này ra như một danh tính. Luật xác thực
    # phía server (Contract B - Step 2) là:
    #
    #     authorized = bool(person_name) and confidence >= FACE_AUTH_THRESHOLD
    #
    # Chuỗi "Unknown" là truthy. Nghĩa là khi SVM nhận diện CHÍNH XÁC rằng đây
    # là người lạ (confidence 0.93), cửa sẽ MỞ: đúng model, đúng ngưỡng, sai
    # kiểu dữ liệu. Xem tests/modules/face/test_face_module_contract.py.
    REJECT_LABELS = {"unknown", "stranger", "other", "nguoi_la", "nguoi la"}

    def __init__(self, model_path: Path | None = None) -> None:
        if model_path is None:
            model_path = MODELS_DIR / "face_model.pkl"

        if not Path(model_path).exists():
            # Fail sớm với thông báo hành động được, thay vì để FileNotFoundError
            # trần bung ra giữa lúc demo.
            raise FileNotFoundError(
                f"Không tìm thấy model nhận diện khuôn mặt: {model_path}\n"
                "Chạy notebook huấn luyện để sinh face_model.pkl rồi đặt vào "
                f"thư mục {MODELS_DIR}."
            )

        # GIẢ ĐỊNH TIN CẬY (ghi lại, không giấu): pickle.load() thực thi mã
        # trong file. Ai ghi được vào models/face_model.pkl thì chiếm được
        # tiến trình gateway. Cùng loại giả định với quyền ghi
        # config/command_schema.json - rủi ro được dời chỗ, không bị loại bỏ.
        with open(model_path, "rb") as f:
            self.clf = pickle.load(f)

    @classmethod
    def _as_identity(cls, name: Any, confidence: float) -> dict[str, Any]:
        """
        Chuyển nhãn thô của SVM thành kết quả theo Contract B.

        Người lạ -> person_name=None, nhưng GIỮ NGUYÊN confidence để face_log
        ghi lại được "nhận ra chắc chắn 0.93 rằng đây là người lạ" phục vụ audit.

        Tách riêng khỏi recognize() để test được mà không cần camera, dlib hay
        file model.
        """
        label = str(name).strip()

        if label.lower() in cls.REJECT_LABELS:
            return {"person_name": None, "confidence": float(confidence)}

        return {"person_name": label, "confidence": float(confidence)}

    def recognize(self, frame: Any) -> dict[str, Any]:
        import cv2
        import face_recognition
        import numpy as np

        if frame is None:
            return {"person_name": None, "confidence": 0.0}

        # OpenCV đọc ảnh BGR, face_recognition cần RGB.
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        face_locations = face_recognition.face_locations(rgb_frame)
        if not face_locations:
            return {"person_name": None, "confidence": 0.0}

        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        if not face_encodings:
            return {"person_name": None, "confidence": 0.0}

        # Dùng khuôn mặt đầu tiên.
        # TODO (bảo mật, chưa làm): nếu len(face_locations) > 1 thì nên từ chối.
        # Hiện tại liveness được xác nhận trên cả khung hình, nên kẻ lạ có thể
        # cầm ảnh in của người nhà đứng cạnh mặt mình rồi tự chớp mắt.
        encoding = np.array(face_encodings[0]).reshape(1, -1)

        try:
            # DÙNG argmax(predict_proba), KHÔNG dùng predict().
            #
            # Với SVC(probability=True), hai thứ đó có thể cho kết quả KHÁC
            # NHAU: predict() dựa trên hàm quyết định one-vs-one, còn
            # predict_proba() dựa trên Platt scaling được hiệu chỉnh bằng một
            # vòng cross-validation riêng. Tài liệu sklearn cảnh báo rõ điều này.
            #
            # Nếu predict() trả lớp A trong khi argmax xác suất là lớp B thì
            # confidence lấy được là proba[A] - KHÔNG phải giá trị lớn nhất -
            # nên có thể tụt dưới ngưỡng dù model khá chắc chắn.
            #
            # Notebook huấn luyện đánh giá bằng argmax(predict_proba). Dùng
            # predict() ở đây nghĩa là con số 95.83% trong evaluation_metrics.txt
            # không mô tả hành vi đang chạy.
            probabilities = self.clf.predict_proba(encoding)[0]
            best_index = int(np.argmax(probabilities))

            name = self.clf.classes_[best_index]
            confidence = probabilities[best_index]

            return self._as_identity(name, confidence)
        except Exception:
            # Fail closed: không phân loại được thì coi như không nhận ra ai.
            return {"person_name": None, "confidence": 0.0}


# =============================================================================
# Liveness detection
# =============================================================================

def eye_aspect_ratio(eye) -> float:
    """
    Tính Eye Aspect Ratio (EAR) từ 6 tọa độ quanh mắt.

    Tỉ lệ chiều dọc / chiều ngang: mắt mở ~0.30, mắt nhắm tụt xuống gần 0.
    """
    # Chiều dọc
    A = math.dist(eye[1], eye[5])
    B = math.dist(eye[2], eye[4])
    # Chiều ngang
    C = math.dist(eye[0], eye[3])
    return (A + B) / (2.0 * C)


def _draw_overlay(
    frame,
    locations,
    require_blink: bool,
    has_blinked: bool,
    ear: float | None,
) -> None:
    """Vẽ khung + trạng thái lên frame hiển thị. Chỉ để người dùng nhìn."""
    import cv2

    if require_blink and not has_blinked:
        status_text = "Please Blink..."
        status_color = (0, 255, 255)  # vàng
    elif require_blink:
        status_text = "Liveness Confirmed!"
        status_color = (0, 255, 0)
    else:
        status_text = "Scanning..."
        status_color = (0, 255, 0)

    for (top, right, bottom, left) in locations:
        cv2.rectangle(frame, (left, top), (right, bottom), status_color, 2)
        cv2.putText(
            frame, status_text, (left, top - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2,
        )
        if ear is not None:
            # Hiển thị EAR để canh EAR_THRESHOLD theo ánh sáng phòng thật.
            cv2.putText(
                frame, f"EAR: {ear:.2f}", (left, bottom + 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, status_color, 1,
            )


def capture_frame(
    cap=None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    require_blink: bool = False,
    show_window: bool = True,
    camera_index: int = 0,
):
    """
    Quét khuôn mặt từ camera, hỗ trợ liveness detection (chớp mắt).

    Hàm đọc NHIỀU frame trong một lần gọi - chớp mắt không thể phát hiện bằng
    một khung hình duy nhất. Frame trả về là khung hình sạch ngay tại thời điểm
    điều kiện được thỏa.

    Args:
        cap: đối tượng cv2.VideoCapture do NGƯỜI GỌI sở hữu và chịu trách nhiệm
            đóng. Truyền None (mặc định) thì hàm tự mở và tự đóng camera - đây
            là đường dùng trong hệ thống thật, giúp webcam rảnh khi không quét.
        timeout_seconds: hết thời gian mà chưa đạt điều kiện -> trả None.
        require_blink: bắt buộc chớp mắt trước khi chấp nhận khung hình.
            Chống tấn công bằng ảnh in hoặc video phát trên điện thoại.
        show_window: tắt khi chạy headless (máy chủ không có màn hình).
        camera_index: chỉ số webcam, chỉ dùng khi cap=None. Máy có webcam rời
            hoặc phần mềm camera ảo (OBS, Zoom, DroidCam) thường đẩy webcam
            thật sang index 1-2; không cấu hình được thì Face Auth hỏng mà
            không có cách nào sửa ngoài việc đổi code.

    Returns:
        Frame SẠCH (không dính nét vẽ) để đưa vào recognize(), hoặc None nếu
        camera lỗi / hết timeout / không bắt được chớp mắt. Người gọi phải coi
        None là "no_face" và TỪ CHỐI - fail closed.
    """
    import time

    import cv2
    import face_recognition

    owns_capture = cap is None
    if owns_capture:
        cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        if owns_capture:
            cap.release()
        return None

    start_time = time.time()
    best_frame = None
    failed_reads = 0

    has_blinked = False
    eyes_were_closed = False

    print(f"[Camera] Đang khởi động quét khuôn mặt (tối đa {timeout_seconds}s)...")

    while time.time() - start_time < timeout_seconds:
        ret, frame = cap.read()
        if not ret:
            failed_reads += 1
            if failed_reads >= MAX_CONSECUTIVE_READ_FAILURES:
                break
            time.sleep(0.05)
            continue
        failed_reads = 0

        # Bản copy sạch gửi cho module nhận diện (không dính nét vẽ).
        clean_frame = frame.copy()

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb_frame)

        current_ear: float | None = None

        if require_blink and locations and not has_blinked:
            landmarks_list = face_recognition.face_landmarks(rgb_frame, locations)

            for landmarks in landmarks_list:
                left_eye = landmarks.get("left_eye")
                right_eye = landmarks.get("right_eye")
                if not (left_eye and right_eye):
                    continue

                current_ear = (
                    eye_aspect_ratio(left_eye) + eye_aspect_ratio(right_eye)
                ) / 2.0

                # Chớp mắt = nhắm RỒI mở lại. Chỉ kiểm tra "đang nhắm" thì một
                # tấm ảnh chụp lúc nhắm mắt cũng qua được.
                if current_ear < EAR_THRESHOLD:
                    eyes_were_closed = True
                elif eyes_were_closed:
                    has_blinked = True
                    break

        if show_window:
            _draw_overlay(frame, locations, require_blink, has_blinked, current_ear)
            cv2.imshow("Face ID Scanner", frame)
            cv2.waitKey(1)

        if locations and (not require_blink or has_blinked):
            best_frame = clean_frame
            if show_window:
                # Giữ hình 1 giây để người dùng kịp thấy thông báo xác nhận.
                cv2.waitKey(1000)
            break

    # Chỉ đóng camera nếu CHÍNH HÀM NÀY mở nó. Đóng camera của người khác sẽ
    # làm mọi lần dùng sau đó thất bại mà không rõ nguyên nhân.
    if owns_capture:
        cap.release()
    if show_window:
        cv2.destroyAllWindows()

    return best_frame

def save_snapshot(frame, prefix: str = "auth") -> str | None:
    """
    Lưu frame đã chụp ra đĩa, trả về TÊN FILE (không phải đường dẫn tuyệt đối).

    Trả tên file để face_log.snapshot_path không dính đường dẫn máy cụ thể -
    DB được sao chép giữa các máy, còn "D:\\253\\DADN\\..." thì không.

    KHÔNG BAO GIỜ raise. Lưu ảnh là việc phụ trợ; nếu đĩa đầy hoặc không có
    quyền ghi thì cửa vẫn phải mở cho người đúng và đóng với người lạ.
    """
    if frame is None:
        return None

    try:
        import cv2
        from datetime import datetime

        from config.settings import SNAPSHOTS_DIR

        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        # Mốc thời gian tới mili giây: hai lần quét cách nhau dưới 1 giây là
        # chuyện bình thường khi người dùng thử lại ngay.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"{prefix}_{stamp}.jpg"

        ok = cv2.imwrite(str(SNAPSHOTS_DIR / filename), frame)
        return filename if ok else None
    except Exception as exc:
        # Nuốt lỗi để không làm sập luồng xác thực, NHƯNG phải để lại dấu vết.
        # `except: return None` trần khiến "chưa quét lần nào" và "lưu ảnh
        # thất bại" không phân biệt được: cả hai đều là thư mục rỗng.
        try:
            from services.logging_service import log_error
            log_error("face", f"save_snapshot failed: {exc}")
        except Exception:
            pass
        return None
    
__all__ = [
    "FaceRecognizer",
    "MockFaceRecognizer",
    "SvmFaceRecognizer",
    "capture_frame",
    "save_snapshot",
    "eye_aspect_ratio",
    "EAR_THRESHOLD",
]