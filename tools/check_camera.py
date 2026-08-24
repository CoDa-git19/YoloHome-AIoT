"""
Kiểm tra webcam và các điều kiện cần cho Face Auth.

Chạy:
    python -m tools.check_camera            # kiểm tra nhanh
    python -m tools.check_camera --scan     # quét thử index 0..4 để tìm webcam
    python -m tools.check_camera --save     # lưu ảnh chụp ra data/ để xem bằng mắt
    python -m tools.check_camera --preview  # mở cửa sổ xem trực tiếp (Esc để thoát)

VÌ SAO CẦN SCRIPT NÀY
---------------------
Face Auth hỏng có thể do 3 lớp rất khác nhau, nhưng trên dashboard đều hiện ra
giống hệt: "lệnh mở cửa bị từ chối". Script tách bạch từng lớp:

    1. OpenCV có mở được webcam không   (phần cứng + driver + CAMERA_INDEX)
    2. Thư viện nhận diện đã sẵn sàng chưa (dlib / face_recognition)
    3. models/face_model.pkl có chưa      (model đã train)

Chỉ cần lớp 1 là đã trả lời được câu "webcam có chạy với dashboard không".
Lớp 2 và 3 quyết định việc NHẬN RA AI, không phải việc mở được camera.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys

from config.settings import CAMERA_INDEX, FACE_MODEL_PATH, DATA_DIR


problems: list[str] = []


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail else ""))
    if not ok:
        problems.append(label)


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# =============================================================================
# Lớp 1: OpenCV + webcam
# =============================================================================

def open_capture(index: int):
    """
    Mở webcam, thử lần lượt các backend.

    Trên Windows, backend mặc định (MSMF) hay bị treo vài giây hoặc trả về
    frame đen với một số webcam; CAP_DSHOW thường ổn định hơn. Thử cả hai để
    không kết luận nhầm là "không có camera".
    """
    import cv2

    backends = [("mặc định", None), ("CAP_DSHOW", getattr(cv2, "CAP_DSHOW", None))]

    for name, backend in backends:
        cap = cv2.VideoCapture(index) if backend is None else cv2.VideoCapture(index, backend)

        if cap.isOpened():
            return cap, name

        cap.release()

    return None, None


def probe_index(index: int) -> tuple[bool, str]:
    """Thử mở một index và đọc thử 1 frame. Trả (thành công, mô tả)."""
    cap, backend = open_capture(index)

    if cap is None:
        return False, "không mở được"

    try:
        # Frame đầu tiên hay bị rỗng khi camera chưa "nóng máy" -> thử vài lần.
        for _ in range(5):
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                return True, f"{w}x{h}, backend={backend}"

        return False, f"mở được nhưng không đọc được frame (backend={backend})"
    finally:
        cap.release()


def check_opencv() -> bool:
    section("LỚP 1: OpenCV và webcam")

    if not has_module("cv2"):
        check("opencv-python đã cài", False, "chạy: pip install opencv-python")
        print("\n  -> Chưa có OpenCV thì KHÔNG thể mở webcam. Cài rồi chạy lại.")
        return False

    import cv2

    check("opencv-python đã cài", True, f"phiên bản {cv2.__version__}")
    return True


def check_camera(index: int, scan: bool) -> bool:
    ok, detail = probe_index(index)
    check(f"Mở được webcam ở CAMERA_INDEX={index}", ok, detail)

    if ok:
        return True

    if not scan:
        print("\n  -> Thử quét các index khác: python -m tools.check_camera --scan")
        return False

    print("\n  Đang quét index 0..4 để tìm webcam khác...")
    found = []

    for i in range(5):
        if i == index:
            continue
        got, det = probe_index(i)
        print(f"    index {i}: {'CÓ  ' + det if got else 'không có'}")
        if got:
            found.append(i)

    if found:
        print(
            f"\n  -> Tìm thấy webcam ở index {found}. "
            f"Đặt CAMERA_INDEX={found[0]} trong .env rồi chạy lại."
        )
    else:
        print(
            "\n  -> Không tìm thấy webcam nào. Kiểm tra: webcam có bị ứng dụng khác "
            "chiếm (Zoom/Teams/OBS) không, và Windows Settings > Privacy > Camera "
            "đã cho phép ứng dụng desktop dùng camera chưa."
        )

    return False


def save_snapshot(index: int) -> None:
    import cv2

    cap, _ = open_capture(index)
    if cap is None:
        print("  Không mở được camera để chụp.")
        return

    try:
        frame = None
        for _ in range(10):
            ok, f = cap.read()
            if ok and f is not None:
                frame = f

        if frame is None:
            print("  Không đọc được frame để lưu.")
            return

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = DATA_DIR / "camera_test.jpg"
        cv2.imwrite(str(out), frame)
        print(f"  Đã lưu ảnh: {out}")
        print("  Mở file này để xác nhận camera chụp đúng hình (không đen, không mờ).")
    finally:
        cap.release()


def preview(index: int) -> None:
    import cv2

    cap, _ = open_capture(index)
    if cap is None:
        print("  Không mở được camera để xem trực tiếp.")
        return

    print("  Đang mở cửa sổ xem trực tiếp. Nhấn Esc để thoát.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            cv2.imshow("Camera test - nhan Esc de thoat", frame)
            if cv2.waitKey(1) == 27:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


# =============================================================================
# Lớp 2 + 3: thư viện nhận diện và model
# =============================================================================

def check_face_stack() -> None:
    section("LỚP 2: Thư viện nhận diện khuôn mặt")

    # Thư mục rỗng `face_recognition/` ở gốc repo hiện ra như một namespace
    # package. Nó CHỈ được dùng khi thư viện thật chưa cài: Python ưu tiên
    # regular package (có __init__.py) trong site-packages hơn namespace
    # directory, kể cả khi thư mục rỗng đứng trước trên sys.path.
    # => Cài thư viện là đủ, KHÔNG cần xoá thư mục.
    spec = importlib.util.find_spec("face_recognition")
    shadowed = False

    if spec is not None and spec.origin is None:
        locations = list(spec.submodule_search_locations or [])
        if locations and "site-packages" not in str(locations[0]):
            shadowed = True
            check(
                "face_recognition trỏ đúng thư viện", False,
                f"đang trỏ vào {locations[0]}",
            )
            print(
                "\n  -> Đang trỏ vào thư mục rỗng ở gốc repo vì thư viện thật\n"
                "     CHƯA được cài. Cài xong nó sẽ tự được ưu tiên:\n"
                "         pip install face_recognition"
            )

    if not shadowed:
        check("face_recognition trỏ đúng thư viện", spec is not None,
              "chưa cài" if spec is None else "")

    check("dlib đã cài", has_module("dlib"), "" if has_module("dlib") else "pip install dlib")
    check("scikit-learn đã cài", has_module("sklearn"),
          "" if has_module("sklearn") else "pip install scikit-learn")

    section("LỚP 3: Model đã train")
    exists = FACE_MODEL_PATH.exists()
    check(
        "models/face_model.pkl", exists,
        str(FACE_MODEL_PATH) if exists else "chưa có - xin file từ người train",
    )


def summary(camera_ok: bool) -> int:
    section("KẾT LUẬN")

    if camera_ok:
        print("  Webcam HOẠT ĐỘNG và dashboard mở được nó.")
    else:
        print("  Webcam CHƯA hoạt động - Face Auth sẽ từ chối mọi lệnh mở cửa.")

    if problems:
        print(f"\n  Còn {len(problems)} mục chưa đạt:")
        for p in problems:
            print(f"    - {p}")
        print(
            "\n  Muốn demo dashboard ngay mà chưa đủ điều kiện, đặt trong .env:\n"
            "      USE_MOCK_FACE=true\n"
            "  (lưu ý: mock LUÔN xác thực thành công - chỉ dùng để demo)"
        )
        return 1

    print("\n  Tất cả điều kiện Face Auth đã sẵn sàng. Đặt USE_MOCK_FACE=false để chạy thật.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.check_camera",
        description="Kiểm tra webcam và điều kiện Face Auth.",
    )
    parser.add_argument("--scan", action="store_true", help="Quét thử index 0..4")
    parser.add_argument("--save", action="store_true", help="Lưu ảnh chụp ra data/")
    parser.add_argument("--preview", action="store_true", help="Mở cửa sổ xem trực tiếp")
    parser.add_argument("--index", type=int, default=None, help="Ép dùng index này")
    args = parser.parse_args(argv)

    index = CAMERA_INDEX if args.index is None else args.index

    if not check_opencv():
        return 1

    camera_ok = check_camera(index, scan=args.scan)

    if camera_ok and args.save:
        section("Chụp ảnh thử")
        save_snapshot(index)

    if camera_ok and args.preview:
        section("Xem trực tiếp")
        preview(index)

    check_face_stack()
    return summary(camera_ok)


if __name__ == "__main__":
    sys.exit(main())
