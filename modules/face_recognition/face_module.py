"""
Face recognition module.

OWNER: Face module.

Vai trò trong pipeline: xác thực khuôn mặt cho các hành động nhạy cảm
(hiện tại là door.open). Module này CHỈ nhận diện khuôn mặt và trả về
độ tin cậy - nó KHÔNG quyết định cho phép hay không.

Quyết định cho phép (so sánh với FACE_AUTH_THRESHOLD) nằm ở
services/auth_service.py, vì ngưỡng là cấu hình phía server.

Xem hợp đồng đầy đủ: docs/Integration-Contracts.md (Contract B).
"""

from __future__ import annotations

import cv2
import pickle
import face_recognition
import os
import math
import numpy as np
from pathlib import Path

from abc import ABC, abstractmethod
from typing import Any

from config.settings import MODELS_DIR


class FaceRecognizer(ABC):
    """
    Interface mà auth_service phụ thuộc vào.

    Concrete implementation gợi ý:
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
                "person_name": str | None,   # None nếu không nhận ra ai
                "confidence": float,          # trong [0.0, 1.0]
            }

        LƯU Ý: KHÔNG tự quyết định authorized ở đây. Trả confidence, để
        auth_service so với FACE_AUTH_THRESHOLD.
        """
        raise NotImplementedError


class MockFaceRecognizer(FaceRecognizer):
    """
    Recognizer giả cho test/demo offline. Không cần camera hay model.

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


class SvmFaceRecognizer(FaceRecognizer):
    """
    Sử dụng thư viện face_recognition để detect và extract embedding,
    sau đó dùng model SVM đã train (từ face_model.pkl) để phân loại.
    """
    def __init__(self, model_path: Path | None = None) -> None:
        if model_path is None:
            model_path = MODELS_DIR / "face_model.pkl"
        
        with open(model_path, "rb") as f:
            self.clf = pickle.load(f)

    def recognize(self, frame: Any) -> dict[str, Any]:
        if frame is None:
            return {"person_name": None, "confidence": 0.0}
            
        # Convert BGR (OpenCV) to RGB (face_recognition)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Detect faces
        face_locations = face_recognition.face_locations(rgb_frame)
        if not face_locations:
            return {"person_name": None, "confidence": 0.0}
            
        # Get embeddings
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        if not face_encodings:
            return {"person_name": None, "confidence": 0.0}
            
        # Dùng khuôn mặt đầu tiên
        encoding = np.array(face_encodings[0]).reshape(1, -1)
        
        # Predict
        try:
            name = self.clf.predict(encoding)[0]
            # Lấy xác suất của class dự đoán
            probabilities = self.clf.predict_proba(encoding)[0]
            class_index = list(self.clf.classes_).index(name)
            confidence = probabilities[class_index]
            
            return {"person_name": name, "confidence": float(confidence)}
        except Exception:
            return {"person_name": None, "confidence": 0.0}


def eye_aspect_ratio(eye):
    """
    Tính toán Eye Aspect Ratio (EAR) dựa trên 6 tọa độ của mắt.
    Dùng để phát hiện nhắm/mở mắt.
    """
    # Chiều dọc
    A = math.dist(eye[1], eye[5])
    B = math.dist(eye[2], eye[4])
    # Chiều ngang
    C = math.dist(eye[0], eye[3])
    return (A + B) / (2.0 * C)


def capture_frame(timeout_seconds: int = 15, require_blink: bool = False):
    """
    Bật camera, hiển thị luồng video trực tiếp để dò khuôn mặt.
    Hỗ trợ Liveness Detection (chớp mắt) nếu require_blink = True.
    Vẽ khung xanh khi quét thấy khuôn mặt, giữ hình 1 giây rồi trả về frame sạch.
    Nếu hết thời gian timeout mà không thấy ai, trả về None.
    """
    import time
    
    cap = cv2.VideoCapture(0)
    start_time = time.time()
    best_frame = None

    print(f"[Camera] Đang khởi động quét khuôn mặt (tối đa {timeout_seconds}s)...")
    
    has_blinked = False
    eyes_were_closed = False
    # Giảm threshold xuống thấp hơn để tránh nhận diện nhầm khi mắt hơi hé (từ 0.22 xuống 0.18)
    EAR_THRESHOLD = 0.21

    while time.time() - start_time < timeout_seconds:
        ret, frame = cap.read()
        if not ret:
            continue

        # Tạo bản copy sạch để gửi về cho module nhận diện (không dính nét vẽ)
        clean_frame = frame.copy()

        # OpenCV đọc ảnh BGR, chuyển sang RGB cho thư viện face_recognition
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Dò tìm vị trí khuôn mặt
        locations = face_recognition.face_locations(rgb_frame)

        # Lấy landmarks nếu yêu cầu chớp mắt
        landmarks_list = []
        if require_blink and locations:
            landmarks_list = face_recognition.face_landmarks(rgb_frame, locations)

        # Vẽ khung cho từng khuôn mặt tìm thấy
        for i, (top, right, bottom, left) in enumerate(locations):
            status_text = "Scanning..."
            status_color = (0, 255, 0) # Xanh lá mặc định

            if require_blink:
                if not has_blinked:
                    status_text = "Please Blink..."
                    status_color = (0, 255, 255) # Vàng
                    
                    if i < len(landmarks_list):
                        landmarks = landmarks_list[i]
                        left_eye = landmarks.get('left_eye')
                        right_eye = landmarks.get('right_eye')
                        
                        if left_eye and right_eye:
                            left_ear = eye_aspect_ratio(left_eye)
                            right_ear = eye_aspect_ratio(right_eye)
                            ear = (left_ear + right_ear) / 2.0
                            
                            # Hiển thị chỉ số EAR lên màn hình để dễ canh chỉnh
                            cv2.putText(frame, f"EAR: {ear:.2f}", (left, bottom + 25), 
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, status_color, 1)
                            
                            if ear < EAR_THRESHOLD:
                                eyes_were_closed = True
                            elif ear >= EAR_THRESHOLD and eyes_were_closed:
                                has_blinked = True
                
                if has_blinked:
                    status_text = "Liveness Confirmed!"
                    status_color = (0, 255, 0)

            cv2.rectangle(frame, (left, top), (right, bottom), status_color, 2)
            cv2.putText(frame, status_text, (left, top - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)

        # Cập nhật cửa sổ hiển thị
        cv2.imshow("Face ID Scanner", frame)
        cv2.waitKey(1)

        # Nếu đã bắt được khuôn mặt và thỏa điều kiện chớp mắt
        if locations:
            if not require_blink or has_blinked:
                best_frame = clean_frame
                # Tạm dừng 1 giây để người dùng nhìn thấy thông báo chốt lại
                cv2.waitKey(1000)
                break

    # Dọn dẹp đóng camera
    cap.release()
    cv2.destroyAllWindows()
    
    return best_frame


__all__ = ["FaceRecognizer", "MockFaceRecognizer", "SvmFaceRecognizer", "capture_frame"]