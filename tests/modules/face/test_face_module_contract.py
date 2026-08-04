"""
Test hợp đồng của Face module (Contract B).

CHẠY HOÀN TOÀN OFFLINE: không webcam, không dlib, không face_model.pkl.
Đó là lý do file này chỉ chạm vào SvmFaceRecognizer._as_identity() và
MockFaceRecognizer - hai thứ duy nhất trong face_module không cần thư viện
thị giác máy tính.

Test quan trọng nhất ở đây tồn tại vì một lý do rất cụ thể: dataset huấn
luyện có class "Unknown", và "Unknown" là một chuỗi truthy. Luật xác thực
phía server là:

    authorized = bool(person_name) and confidence >= FACE_AUTH_THRESHOLD

Nên nếu SVM nhận diện CHÍNH XÁC rằng đây là người lạ (confidence 0.93) mà
module trả thẳng nhãn đó ra, thì cửa chính sẽ mở cho người lạ - đúng model,
đúng ngưỡng, sai kiểu dữ liệu.
"""

from __future__ import annotations

import pytest

from config.settings import FACE_AUTH_THRESHOLD
from modules.face_recognition.face_module import (
    MockFaceRecognizer,
    SvmFaceRecognizer,
)


def is_authorized(result: dict) -> bool:
    """
    Tái hiện đúng luật quyết định của server (Contract B - Step 2).

    Giữ nguyên một bản sao ở đây là có chủ ý: nếu ai đó sửa luật trong
    auth_service mà quên file này, khác biệt sẽ lộ ra.
    """
    return (
        result.get("person_name") is not None
        and float(result.get("confidence") or 0.0) >= FACE_AUTH_THRESHOLD
    )


# =============================================================================
# 1. Nhãn "người lạ" không bao giờ được thành danh tính
# =============================================================================

@pytest.mark.parametrize(
    "label",
    ["Unknown", "unknown", "UNKNOWN", "  Unknown  ", "stranger", "nguoi_la"],
)
def test_reject_labels_never_become_an_identity(label: str) -> None:
    result = SvmFaceRecognizer._as_identity(label, 0.99)

    assert result["person_name"] is None, (
        f"Nhãn {label!r} bị trả ra như một danh tính hợp lệ."
    )


def test_unknown_with_high_confidence_is_denied() -> None:
    """
    Ca nguy hiểm nhất: model RẤT CHẮC CHẮN đây là người lạ.

    Nếu test này đỏ, người lạ mở được cửa chính.
    """
    result = SvmFaceRecognizer._as_identity("Unknown", 0.99)

    assert is_authorized(result) is False, (
        "NGHIÊM TRỌNG: người lạ được cấp quyền mở cửa."
    )


def test_rejected_identity_keeps_confidence_for_audit() -> None:
    """
    person_name=None nhưng confidence phải giữ nguyên.

    face_log cần ghi được "nhận ra chắc chắn 0.93 rằng đây là người lạ".
    Nuốt mất con số này thì bảng audit chỉ còn toàn số 0.
    """
    result = SvmFaceRecognizer._as_identity("Unknown", 0.93)

    assert result["confidence"] == pytest.approx(0.93)


# =============================================================================
# 2. Người nhà vẫn phải qua được
# =============================================================================

def test_real_member_passes() -> None:
    result = SvmFaceRecognizer._as_identity("Uyen", 0.91)

    assert result["person_name"] == "Uyen"
    assert result["confidence"] == pytest.approx(0.91)
    assert is_authorized(result) is True


def test_member_name_is_trimmed_but_not_altered() -> None:
    """Tên thư mục dataset có thể dính khoảng trắng; nhãn phải sạch."""
    result = SvmFaceRecognizer._as_identity("  Nha Uyen  ", 0.88)

    assert result["person_name"] == "Nha Uyen"


def test_member_below_threshold_is_denied() -> None:
    """Ngưỡng là quyết định của SERVER, không phải của face module."""
    result = SvmFaceRecognizer._as_identity("Uyen", FACE_AUTH_THRESHOLD - 0.01)

    assert result["person_name"] == "Uyen", "Module không được tự ý xoá danh tính."
    assert is_authorized(result) is False


# =============================================================================
# 3. Kiểu dữ liệu trả về đúng hợp đồng
# =============================================================================

def test_identity_result_shape() -> None:
    result = SvmFaceRecognizer._as_identity("Uyen", 0.91)

    assert set(result) == {"person_name", "confidence"}
    assert isinstance(result["confidence"], float)


def test_confidence_is_float_even_from_numpy_like_input() -> None:
    """
    predict_proba() của sklearn trả numpy.float64, không phải float thuần.

    Nếu để lọt kiểu numpy vào log_face(), lớp SQLite sẽ ném lỗi
    "Error binding parameter" - và nó chỉ nổ ĐÚNG LÚC có người đứng
    trước camera, không bao giờ nổ trong test.
    """

    class FakeNumpyFloat(float):
        pass

    result = SvmFaceRecognizer._as_identity("Uyen", FakeNumpyFloat(0.91))

    assert type(result["confidence"]) is float


# =============================================================================
# 4. MockFaceRecognizer vẫn dùng được offline
# =============================================================================

def test_mock_recognizer_needs_no_camera() -> None:
    """
    Test này gián tiếp khoá lại nguyên tắc lazy import.

    Import được face_module trên máy KHÔNG cài dlib chính là điều đang được
    kiểm tra - nếu ai đó đưa `import cv2` trở lại đầu file, cả file test này
    sẽ đỏ ngay ở khâu collection.
    """
    recognizer = MockFaceRecognizer("member_1", 0.91)
    result = recognizer.recognize(None)

    assert is_authorized(result) is True


def test_mock_recognizer_can_simulate_no_face() -> None:
    recognizer = MockFaceRecognizer(None, 0.0)

    assert is_authorized(recognizer.recognize(None)) is False