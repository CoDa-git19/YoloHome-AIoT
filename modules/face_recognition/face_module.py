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

from abc import ABC, abstractmethod
from typing import Any


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


# TODO(Face owner): implement SvmFaceRecognizer.
#
# class SvmFaceRecognizer(FaceRecognizer):
#     def __init__(self, model_path: Path = MODELS_DIR / "face_model.pkl") -> None:
#         # load dlib detector + embedding model + SVM classifier từ model_path
#         ...
#
#     def recognize(self, frame) -> dict[str, Any]:
#         # 1. detect face trong frame
#         # 2. nếu không có mặt -> {"person_name": None, "confidence": 0.0}
#         # 3. tính embedding -> SVM predict + xác suất
#         # 4. return {"person_name": name, "confidence": prob}
#         raise NotImplementedError
#
# TODO(Face owner): hàm lấy frame từ camera (OpenCV VideoCapture) cho main.py.


__all__ = ["FaceRecognizer", "MockFaceRecognizer"]
