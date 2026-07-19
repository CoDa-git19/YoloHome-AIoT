"""
modules/face_recognition/face_module.py
========================================
Module nhận diện khuôn mặt real-time cho hệ thống YoloHome-AIoT.

Pipeline:
    webcam frame
    → dlib face detection
    → face_recognition 128-D embedding
    → SVM classifier (face_model.pkl)
    → [Liveness: EAR blink check]
    → verify_face() → dict

Output chuẩn của verify_face():
    {
        "person_name": "Nguyen_Dinh_Khang",
        "confidence":  0.91,
        "authorized":  True
    }

Tính năng:
    - Vẽ bounding box + tên người + confidence lên màn hình (tùy chọn).
    - Liveness detection: yêu cầu chớp mắt ít nhất 1 lần (EAR threshold)
      để chặn tấn công dùng ảnh in ra giấy.
    - Được gọi bởi services/auth_service.py khi next_step == "auth_required".
    - Kết quả được log vào face_log bởi logging_service.log_face().
    - Ngưỡng xác thực đọc từ config.settings.FACE_AUTH_THRESHOLD (.env).
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Đường dẫn model pkl (cùng thư mục với file này)
# ---------------------------------------------------------------------------
_MODULE_DIR = Path(__file__).resolve().parent
_DEFAULT_MODEL_PATH = _MODULE_DIR / "face_model.pkl"


# ---------------------------------------------------------------------------
# Lazy-load thư viện nặng để không block import khi không cần module này
# ---------------------------------------------------------------------------

def _require_face_recognition() -> Any:
    """Import face_recognition, báo lỗi rõ ràng nếu chưa cài."""
    try:
        import face_recognition  # type: ignore
        return face_recognition
    except ImportError as exc:
        raise ImportError(
            "Thư viện 'face_recognition' chưa được cài đặt.\n"
            "Chạy: pip install face_recognition\n"
            "(Yêu cầu dlib và C++ Build Tools đã được cài sẵn.)"
        ) from exc


def _require_dlib() -> Any:
    """Import dlib (cần cho Liveness Detection 68-landmark)."""
    try:
        import dlib  # type: ignore
        return dlib
    except ImportError as exc:
        raise ImportError(
            "Thư viện 'dlib' chưa được cài đặt.\n"
            "Chạy: pip install dlib\n"
            "(Yêu cầu CMake và C++ Build Tools.)"
        ) from exc


# ---------------------------------------------------------------------------
# Liveness Detection – Eye Aspect Ratio (EAR)
# ---------------------------------------------------------------------------
# Chỉ số EAR: tỷ lệ chiều cao / chiều rộng của mắt.
# Khi mắt mở bình thường EAR ≈ 0.25–0.30.
# Khi chớp mắt EAR giảm xuống < EAR_BLINK_THRESHOLD.
#
# Chỉ số mắt (từ dlib 68-point model):
#   Mắt phải: landmark 36-41
#   Mắt trái:  landmark 42-47
_EAR_BLINK_THRESHOLD = 0.25   # EAR dưới giá trị này → đang nhắm mắt
_EAR_CONSEC_FRAMES   = 2       # Số frame liên tiếp EAR < threshold → 1 lần chớp

# Đường dẫn shape predictor (68-landmark model của dlib)
_SHAPE_PREDICTOR_PATH = _MODULE_DIR / "shape_predictor_68_face_landmarks.dat"

# Đường dẫn file .dat landmarks mặc định của dlib
# Nếu không có file, liveness detection tự động bị bỏ qua (không crash)


def _compute_ear(eye_landmarks: np.ndarray) -> float:
    """
    Tính Eye Aspect Ratio (EAR) từ 6 điểm landmark của mắt.

    Công thức (Soukupová & Čech, 2016):
        EAR = (||p2-p6|| + ||p3-p5||) / (2 * ||p1-p4||)

    Args:
        eye_landmarks: np.ndarray shape (6, 2) - tọa độ 6 điểm landmark.

    Returns:
        float EAR trong khoảng [0.0, ~0.4].
    """
    # Khoảng cách dọc (2 cặp)
    A = np.linalg.norm(eye_landmarks[1] - eye_landmarks[5])
    B = np.linalg.norm(eye_landmarks[2] - eye_landmarks[4])
    # Khoảng cách ngang
    C = np.linalg.norm(eye_landmarks[0] - eye_landmarks[3])
    return (A + B) / (2.0 * C) if C > 0 else 0.0


class LivenessDetector:
    """
    Phát hiện "sự sống" của khuôn mặt bằng cách yêu cầu chớp mắt.

    Hoạt động:
        - Dùng dlib 68-point predictor để lấy tọa độ mắt.
        - Tính EAR mỗi frame.
        - Nếu EAR < _EAR_BLINK_THRESHOLD trong ít nhất _EAR_CONSEC_FRAMES
          liên tiếp rồi tăng lên → đếm 1 lần chớp mắt.
        - Yêu cầu ít nhất required_blinks lần chớp mắt → is_alive = True.

    Nếu file shape_predictor_68_face_landmarks.dat không tồn tại,
    liveness luôn trả True (degraded mode, log cảnh báo).
    """

    # Index landmark trong model 68-point (0-indexed)
    _RIGHT_EYE_IDX = list(range(36, 42))  # landmark 36–41
    _LEFT_EYE_IDX  = list(range(42, 48))  # landmark 42–47

    def __init__(self, required_blinks: int = 1) -> None:
        self.required_blinks = required_blinks
        self._predictor = self._load_predictor()
        self._detector  = None   # dlib face detector (lazy)
        self.reset()

    def reset(self) -> None:
        """Reset bộ đếm chớp mắt. Gọi trước mỗi phiên xác thực."""
        self._blink_count   = 0
        self._consec_below  = 0   # số frame liên tiếp EAR < threshold
        self._eye_closed    = False

    @property
    def is_alive(self) -> bool:
        """True khi đã đạt đủ số lần chớp mắt yêu cầu."""
        return self._blink_count >= self.required_blinks

    @property
    def blink_count(self) -> int:
        return self._blink_count

    def available(self) -> bool:
        """Trả True nếu shape predictor đã được load thành công."""
        return self._predictor is not None

    def update(self, rgb_frame: np.ndarray, face_location: tuple) -> float | None:
        """
        Cập nhật trạng thái liveness từ một frame.

        Args:
            rgb_frame:     Frame RGB (đã đổi màu từ BGR).
            face_location: Tuple (top, right, bottom, left) từ face_recognition.

        Returns:
            EAR trung bình của frame, hoặc None nếu không tính được.
        """
        if self._predictor is None:
            return None

        try:
            dlib = _require_dlib()
        except ImportError:
            return None

        # Chuyển face_location sang dlib rectangle
        top, right, bottom, left = face_location
        rect = dlib.rectangle(left, top, right, bottom)

        # Lấy 68 landmark
        gray = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2GRAY)
        shape = self._predictor(gray, rect)
        coords = np.array([[shape.part(i).x, shape.part(i).y] for i in range(68)])

        right_eye = coords[self._RIGHT_EYE_IDX]
        left_eye  = coords[self._LEFT_EYE_IDX]

        right_ear = _compute_ear(right_eye)
        left_ear  = _compute_ear(left_eye)
        avg_ear   = (right_ear + left_ear) / 2.0

        # Phát hiện chớp mắt
        if avg_ear < _EAR_BLINK_THRESHOLD:
            self._consec_below += 1
            self._eye_closed = True
        else:
            # Mắt vừa mở lại sau khi nhắm
            if self._eye_closed and self._consec_below >= _EAR_CONSEC_FRAMES:
                self._blink_count += 1
                logger.debug(
                    "Liveness: phát hiện chớp mắt lần %d (EAR=%.3f)",
                    self._blink_count, avg_ear,
                )
            self._consec_below = 0
            self._eye_closed = False

        return avg_ear

    @staticmethod
    def _load_predictor() -> Any | None:
        """Load dlib shape predictor. Trả None nếu file không tồn tại."""
        if not _SHAPE_PREDICTOR_PATH.exists():
            logger.warning(
                "shape_predictor_68_face_landmarks.dat không tìm thấy tại %s. "
                "Liveness detection bị TẮT (degraded mode). "
                "Tải file tại: http://dlib.net/files/shape_predictor_68_face_landmarks.dat.bz2",
                _SHAPE_PREDICTOR_PATH,
            )
            return None

        try:
            dlib = _require_dlib()
            predictor = dlib.shape_predictor(str(_SHAPE_PREDICTOR_PATH))
            logger.info("Liveness detection: shape predictor loaded.")
            return predictor
        except Exception as exc:
            logger.error("Không load được shape predictor: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Overlay helpers (vẽ UI lên frame)
# ---------------------------------------------------------------------------

def _draw_overlay(
    frame: np.ndarray,
    face_location: tuple,
    person_name: str,
    confidence: float,
    authorized: bool,
    ear: float | None = None,
    blink_count: int = 0,
    liveness_required: bool = False,
    is_alive: bool = True,
) -> None:
    """
    Vẽ bounding box + thông tin nhận diện + EAR lên frame (in-place).

    - Authorized     → khung xanh lá + tên người
    - Not authorized → khung đỏ + "Access Denied"
    - Unknown        → khung vàng
    """
    top, right, bottom, left = face_location

    # Màu khung
    if authorized:
        box_color = (0, 200, 0)          # Xanh lá
    elif person_name == "Unknown":
        box_color = (0, 200, 255)        # Vàng
    else:
        box_color = (0, 0, 220)          # Đỏ

    # Bounding box
    cv2.rectangle(frame, (left, top), (right, bottom), box_color, 2)

    # Nhãn chính: tên người + confidence
    label = f"{person_name} ({confidence:.0%})"
    label_y = top - 10 if top > 30 else bottom + 20
    cv2.putText(
        frame, label,
        (left, label_y),
        cv2.FONT_HERSHEY_DUPLEX, 0.6,
        box_color, 1, cv2.LINE_AA,
    )

    # Liveness info (nếu bật)
    if liveness_required:
        liveness_text = (
            f"Blinks: {blink_count}  EAR: {ear:.3f}" if ear is not None
            else "Liveness: OFF"
        )
        status_text = "ALIVE" if is_alive else "Blink to verify"
        status_color = (0, 200, 0) if is_alive else (0, 165, 255)

        cv2.putText(
            frame, liveness_text,
            (left, bottom + 18),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA,
        )
        cv2.putText(
            frame, status_text,
            (left, bottom + 36),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, status_color, 1, cv2.LINE_AA,
        )


# ---------------------------------------------------------------------------
# FaceRecognizer – class chính
# ---------------------------------------------------------------------------
class FaceRecognizer:
    """
    Nhận diện khuôn mặt dùng dlib 128-D embedding + SVM classifier.

    Model đã train (face_model.pkl) gồm 5 thành viên + class "Unknown":
        - Huynh_Cam_ly
        - Huynh_Viet_Cong_Danh
        - Le_Nhu_Nha_Uyen
        - Nguyen_Dinh_Khang
        - Nguyen_Thanh_Binh
        - Unknown

    Kết quả đánh giá: F1=98.35%, FAR=0.00%, FRR=2.00%.

    Sử dụng:
        recognizer = FaceRecognizer()
        result = recognizer.verify_face(frame)
        # {"person_name": "Nguyen_Dinh_Khang", "confidence": 0.91, "authorized": True}
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        threshold: float | None = None,
    ) -> None:
        """
        Args:
            model_path: Đường dẫn tới face_model.pkl. Mặc định dùng file
                        cùng thư mục modules/face_recognition/.
            threshold:  Ngưỡng confidence để coi là authorized.
                        Mặc định đọc từ FACE_AUTH_THRESHOLD trong .env (0.80).
        """
        self._model_path = Path(model_path) if model_path else _DEFAULT_MODEL_PATH
        self._threshold = threshold if threshold is not None else self._load_threshold()
        self._classifier = self._load_model()
        self._fr = _require_face_recognition()

        logger.info(
            "FaceRecognizer khởi tạo | model=%s | threshold=%.2f | classes=%s",
            self._model_path.name,
            self._threshold,
            list(self._classifier.classes_),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_face(self, frame: np.ndarray) -> dict[str, Any]:
        """
        Nhận diện khuôn mặt từ một frame webcam (BGR format của OpenCV).

        Args:
            frame: np.ndarray BGR image từ cv2.VideoCapture.read().

        Returns:
            {
                "person_name": str,   # Tên thành viên hoặc "Unknown"
                "confidence":  float, # Xác suất trong [0.0, 1.0]
                "authorized":  bool,  # True nếu confidence >= threshold
                                      # và person_name != "Unknown"
            }

        Nếu không phát hiện khuôn mặt nào:
            {"person_name": "Unknown", "confidence": 0.0, "authorized": False}
        """
        if frame is None or frame.size == 0:
            logger.warning("verify_face: frame rỗng hoặc None.")
            return self._no_face_result()

        # face_recognition dùng RGB, OpenCV trả BGR → phải đổi màu
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Bước 1: Phát hiện vị trí khuôn mặt trong ảnh
        face_locations = self._fr.face_locations(rgb_frame, model="hog")

        if not face_locations:
            logger.debug("verify_face: không phát hiện khuôn mặt.")
            return self._no_face_result()

        # Bước 2: Trích xuất embedding 128-D
        # Chỉ lấy khuôn mặt ĐẦU TIÊN (lớn nhất / gần camera nhất)
        face_encodings = self._fr.face_encodings(rgb_frame, [face_locations[0]])

        if not face_encodings:
            logger.debug("verify_face: không trích xuất được embedding.")
            return self._no_face_result()

        encoding = face_encodings[0].reshape(1, -1)

        # Bước 3: SVM phân loại
        try:
            print("SVM")
            probabilities = self._classifier.predict_proba(encoding)[0]
            class_idx = int(np.argmax(probabilities))
            confidence = float(probabilities[class_idx])
            person_name = str(self._classifier.classes_[class_idx])
        except Exception as exc:
            logger.error("verify_face: SVM predict_proba thất bại: %s", exc)
            return self._no_face_result()

        # Bước 4: Quyết định authorized dựa trên threshold
        # "Unknown" KHÔNG BAO GIỜ được authorized, bất kể confidence
        authorized = confidence >= self._threshold and person_name != "Unknown"

        logger.info(
            "Nhận diện: person=%s | confidence=%.4f | authorized=%s | threshold=%.2f",
            person_name,
            confidence,
            authorized,
            self._threshold,
        )

        return {
            "person_name": person_name,
            "confidence": round(confidence, 4),
            "authorized": authorized,
        }

    def process_frame_with_liveness(
        self,
        frame: np.ndarray,
        liveness_detector: "LivenessDetector | None" = None,
        display: bool = False,
    ) -> dict[str, Any]:
        """
        Xử lý MỘT frame: nhận diện khuôn mặt + cập nhật liveness state.

        Module này CHỈ làm việc với frame đơn lẻ.
        Vòng lặp camera, timeout và quản lý VideoCapture là trách nhiệm
        của lớp gọi (auth_service.py).

        Args:
            frame:             np.ndarray BGR từ cv2.VideoCapture.read().
            liveness_detector: Đối tượng LivenessDetector đang theo dõi phiên
                               xác thực. Truyền None để bỏ qua liveness.
            display:           Nếu True, vẽ bounding box lên frame in-place
                               (để auth_service có thể gọi cv2.imshow sau).

        Returns:
            {
                "person_name": str,
                "confidence":  float,
                "authorized":  bool,   # SVM pass VÀ liveness pass (nếu bật)
                "face_found":  bool,   # False nếu không phát hiện khuôn mặt
                "ear":         float | None,  # EAR của frame (None nếu không tính được)
            }
        """
        if frame is None or frame.size == 0:
            logger.warning("process_frame_with_liveness: frame rong hoac None.")
            return {**self._no_face_result(), "face_found": False, "ear": None}

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = self._fr.face_locations(rgb_frame, model="hog")

        if not face_locations:
            return {**self._no_face_result(), "face_found": False, "ear": None}

        face_encodings = self._fr.face_encodings(rgb_frame, [face_locations[0]])

        if not face_encodings:
            return {**self._no_face_result(), "face_found": True, "ear": None}

        encoding = face_encodings[0].reshape(1, -1)
        # ── SVM phân loại ─────────────────────────────────────────────
        try:
            probs = self._classifier.predict_proba(encoding)[0]
            idx  = int(np.argmax(probs))
            conf = float(probs[idx])
            name = str(self._classifier.classes_[idx])
        except Exception as exc:
            logger.error("process_frame_with_liveness: SVM that bai: %s", exc)
            return {**self._no_face_result(), "face_found": True, "ear": None}

        authorized_by_svm = conf >= self._threshold and name != "Unknown"

        # ── Liveness Detection (cập nhật state bên ngoài) ─────────────
        ear: float | None = None
        if liveness_detector is not None and authorized_by_svm:
            ear = liveness_detector.update(rgb_frame, face_locations[0])

        liveness_ok = (
            liveness_detector is None
            or liveness_detector.is_alive
        )
        final_authorized = authorized_by_svm and liveness_ok

        logger.debug(
            "Frame: person=%s conf=%.3f svm=%s live=%s final=%s ear=%s",
            name, conf, authorized_by_svm, liveness_ok, final_authorized,
            f"{ear:.3f}" if ear is not None else "N/A",
        )

        # ── Vẽ overlay lên frame nếu display=True ─────────────────────
        if display:
            blink_count = liveness_detector.blink_count if liveness_detector else 0
            _draw_overlay(
                frame=frame,
                face_location=face_locations[0],
                person_name=name,
                confidence=conf,
                authorized=final_authorized,
                ear=ear,
                blink_count=blink_count,
                liveness_required=(liveness_detector is not None),
                is_alive=liveness_ok,
            )

        return {
            "person_name": name,
            "confidence":  round(conf, 4),
            "authorized":  final_authorized,
            "face_found":  True,
            "ear":         round(ear, 4) if ear is not None else None,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_threshold() -> float:
        """Đọc FACE_AUTH_THRESHOLD từ config.settings. Fallback về 0.80."""
        try:
            from config.settings import FACE_AUTH_THRESHOLD
            return float(FACE_AUTH_THRESHOLD)
        except Exception:
            logger.warning(
                "Không đọc được FACE_AUTH_THRESHOLD. Dùng mặc định 0.80."
            )
            return 0.80

    def _load_model(self):
        """
        Load SVM model từ file .pkl.

        Kiểm tra model có đúng interface không (sklearn SVC với probability=True).
        Nếu pkl sai format → raise ValueError ngay lúc khởi động, không phải giữa demo.
        """
        if not self._model_path.exists():
            raise FileNotFoundError(
                f"Không tìm thấy file model: {self._model_path}\n"
                "Đảm bảo face_model.pkl nằm trong modules/face_recognition/."
            )

        with open(self._model_path, "rb") as f:
            model = pickle.load(f)

        # Kiểm tra interface: cần predict_proba (SVC(probability=True)) và classes_
        if not hasattr(model, "predict_proba"):
            raise ValueError(
                f"{self._model_path.name}: model thiếu predict_proba().\n"
                "Huấn luyện lại SVC với tham số probability=True."
            )
        if not hasattr(model, "classes_"):
            raise ValueError(
                f"{self._model_path.name}: model thiếu classes_ attribute. "
                "Đảm bảo dùng scikit-learn SVC."
            )

        return model

    @staticmethod
    def _no_face_result() -> dict[str, Any]:
        """Kết quả mặc định khi không nhận diện được khuôn mặt."""
        return {
            "person_name": "Unknown",
            "confidence": 0.0,
            "authorized": False,
        }


# ---------------------------------------------------------------------------
# Module-level singleton + shortcut functions
# (gọi trực tiếp mà không cần khởi tạo FaceRecognizer thủ công)
# ---------------------------------------------------------------------------

_recognizer: FaceRecognizer | None = None


def _get_recognizer() -> FaceRecognizer:
    """Lazy-init singleton. Load model một lần duy nhất cho toàn bộ process."""
    global _recognizer
    if _recognizer is None:
        _recognizer = FaceRecognizer()
    return _recognizer


def verify_face(frame: np.ndarray) -> dict[str, Any]:
    """
    Shortcut: nhận diện khuôn mặt từ 1 frame BGR.

    Args:
        frame: np.ndarray BGR từ cv2.VideoCapture.read().

    Returns:
        {"person_name": str, "confidence": float, "authorized": bool}
    """
    return _get_recognizer().verify_face(frame)


def process_frame_with_liveness(
    frame: np.ndarray,
    liveness_detector: "LivenessDetector | None" = None,
    display: bool = False,
) -> dict[str, Any]:
    """
    Shortcut: xử lý 1 frame kèm liveness state (nếu có).

    Được gọi bởi auth_service trong vòng lặp camera.
    auth_service tự quản lý VideoCapture, timeout và vòng lặp.

    Args:
        frame:             np.ndarray BGR từ cv2.VideoCapture.read().
        liveness_detector: Đối tượng LivenessDetector cho phiên xác thực
                           đang chạy. None = bỏ qua liveness.
        display:           Nếu True, vẽ overlay lên frame in-place.

    Returns:
        {
            "person_name": str,
            "confidence":  float,
            "authorized":  bool,
            "face_found":  bool,
            "ear":         float | None,
        }
    """
    return _get_recognizer().process_frame_with_liveness(
        frame=frame,
        liveness_detector=liveness_detector,
        display=display,
    )

