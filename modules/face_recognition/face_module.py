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


def capture_frame():
    """
    Hàm hỗ trợ lấy một frame từ camera mặc định (OpenCV).
    """
    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()
    cap.release()
    if ret:
        return frame
    return None


__all__ = ["FaceRecognizer", "MockFaceRecognizer", "SvmFaceRecognizer", "capture_frame"]
