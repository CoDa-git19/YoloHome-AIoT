"""
services/auth_service.py
=========================
Dịch vụ xác thực khuôn mặt cho các lệnh nhạy cảm (auth_required).

Luồng hoạt động:
    1. CommandService xử lý lệnh và phát hiện next_step == "auth_required".
    2. CommandService (hoặc Dashboard/API) gọi AuthService.authenticate_and_execute().
    3. AuthService gọi face_module.verify_from_camera() để mở camera xác thực.
    4. Kết quả được ghi vào bảng face_log.
    5. Nếu authorized → gọi command_service.execute_authorized_command().
    6. Nếu denied / timeout → báo lỗi, KHÔNG thực thi lệnh.

Đảm bảo an ninh:
    - AuthService KHÔNG tự quyết định threshold. Threshold đọc từ .env
      qua face_module (config.settings.FACE_AUTH_THRESHOLD).
    - "Unknown" KHÔNG BAO GIỜ được authorized, dù confidence cao.
    - Mọi lần thử xác thực đều được log đầy đủ vào face_log.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from services.logging_service import log_face, log_error

if TYPE_CHECKING:
    # Import kiểu để tránh circular import (CommandService cũng import AuthService)
    from services.command_service import CommandService

logger = logging.getLogger(__name__)


class AuthService:
    """
    Dịch vụ quản lý luồng xác thực khuôn mặt.

    Sử dụng:
        auth = AuthService(command_service=cs)
        result = auth.authenticate_and_execute(command_data, command_id=42)

    Inject command_service từ ngoài vào để tránh circular import và
    dễ mock khi test.
    """

    def __init__(
        self,
        command_service: "CommandService | None" = None,
        camera_index: int = 0,
        timeout_seconds: float = 10.0,
        required_frames: int = 3,
        use_liveness: bool = True,
        required_blinks: int = 1,
        display: bool = False,
        window_title: str = "YoloHome - Face Auth",
    ) -> None:
        """
        Args:
            command_service:  CommandService sẽ thực thi lệnh sau khi xác thực.
                              Có thể inject sau bằng auth.command_service = cs.
            camera_index:     Index webcam (0 = mặc định).
            timeout_seconds:  Timeout xác thực khuôn mặt (giây).
            required_frames:  Số frame nhất quán để kết luận authorized.
            use_liveness:     Bật/tắt liveness detection (chớp mắt).
                              Tự động tắt nếu thiếu shape_predictor_68_face_landmarks.dat.
            required_blinks:  Số lần chớp mắt tối thiểu (khi use_liveness=True).
            display:          Hiển thị cửa sổ OpenCV real-time (dùng khi demo/debug).
            window_title:     Tiêu đề cửa sổ hiển thị.
        """
        self.command_service  = command_service
        self.camera_index     = camera_index
        self.timeout_seconds  = timeout_seconds
        self.required_frames  = required_frames
        self.use_liveness     = use_liveness
        self.required_blinks  = required_blinks
        self.display          = display
        self.window_title     = window_title


    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def authenticate_and_execute(
        self,
        command_data: dict[str, Any],
        command_id: int | None = None,
        triggered_by: str = "sensitive_command",
    ) -> dict[str, Any]:
        """
        Xác thực khuôn mặt rồi thực thi lệnh nếu authorized.

        Args:
            command_data:  JSON command đã được validate (device, action, room, ...).
            command_id:    ID của command_log row liên quan (để liên kết face_log).
            triggered_by:  Lý do kích hoạt xác thực (dùng để audit log).

        Returns:
            {
                "authorized":    bool,
                "person_name":   str,
                "confidence":    float,
                "executed":      bool,   # True nếu lệnh đã được gửi xuống phần cứng
                "response":      str,    # Thông báo trả về người dùng
            }
        """
        device = command_data.get("device", "")
        room = command_data.get("room", "")
        action = command_data.get("action", "")

        logger.info(
            "Bắt đầu xác thực khuôn mặt | command=%s.%s@%s | command_id=%s",
            device, action, room, command_id,
        )

        # Bước 1: Gọi module nhận diện khuôn mặt
        face_result = self._run_face_verification()

        person_name = face_result["person_name"]
        confidence = face_result["confidence"]
        authorized = face_result["authorized"]

        # Bước 2: Xác định status cho face_log
        if person_name == "Unknown" and confidence == 0.0:
            # Không phát hiện được khuôn mặt nào hoặc timeout
            face_status = "no_face"
        elif authorized:
            face_status = "authorized"
        else:
            face_status = "denied"

        # Bước 3: Ghi log xác thực vào face_log (bất kể kết quả)
        self._log_face_event(
            person_name=person_name,
            confidence=confidence,
            status=face_status,
            command_id=command_id,
            triggered_by=triggered_by,
            device=device,
            room=room,
        )

        # Bước 4: Nếu authorized → thực thi lệnh
        if authorized:
            executed = self._execute_command(command_data)

            action_result = "executed" if executed else "failed"
            self._update_face_log_action(
                person_name=person_name,
                confidence=confidence,
                command_id=command_id,
                action_result=action_result,
                device=device,
                room=room,
            )

            if executed:
                response = (
                    f"Xác thực khuôn mặt thành công ({person_name}). "
                    f"Đã thực thi lệnh: {action} {device} tại {room}."
                )
                logger.info("Lệnh %s.%s@%s thực thi thành công.", device, action, room)
            else:
                response = (
                    f"Xác thực khuôn mặt thành công ({person_name}), "
                    "nhưng không thể gửi lệnh đến phần cứng."
                )
                logger.error("Thực thi lệnh thất bại sau khi xác thực.")

            return {
                "authorized": True,
                "person_name": person_name,
                "confidence": confidence,
                "executed": executed,
                "response": response,
            }

        # Bước 5: Từ chối
        if face_status == "no_face":
            response = (
                "Không phát hiện khuôn mặt trong thời gian chờ. "
                "Lệnh bị hủy. Vui lòng thử lại và nhìn vào camera."
            )
        else:
            response = (
                f"Xác thực không thành công (confidence={confidence:.2f}). "
                "Lệnh bị từ chối vì danh tính không được nhận diện."
            )

        logger.warning(
            "Từ chối xác thực | person=%s | confidence=%.4f | status=%s",
            person_name, confidence, face_status,
        )

        return {
            "authorized": False,
            "person_name": person_name,
            "confidence": confidence,
            "executed": False,
            "response": response,
        }

    def verify_only(self) -> dict[str, Any]:
        """
        Chỉ chạy nhận diện, KHÔNG thực thi lệnh.

        Dùng cho trường hợp Dashboard muốn test webcam hoặc
        manual authentication check.

        Returns:
            {"person_name": str, "confidence": float, "authorized": bool}
        """
        return self._run_face_verification()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_face_verification(self) -> dict[str, Any]:
        """
        Quản lý toàn bộ vòng lặp camera để xác thực khuôn mặt.

        Trách nhiệm của auth_service (KHÔNG thuộc face_module):
            - Mở và đóng VideoCapture.
            - Vòng lặp đọc frame + timeout.
            - Đếm số frame nhất quán (required_frames).
            - Khởi tạo và reset LivenessDetector theo phiên.
            - Hiển thị cửa sổ OpenCV (nếu cấu hình display=True).

        face_module chỉ làm 1 việc: nhận 1 frame → trả kết quả dict.
        """
        import time
        import cv2 as _cv2  # alias để tránh shadow outer scope

        try:
            from modules.face_recognition.face_module import (
                process_frame_with_liveness,
                LivenessDetector,
            )
        except ImportError:
            logger.error(
                "Khong the import face_module. "
                "Kiem tra thu vien face_recognition va dlib da duoc cai dat chua."
            )
            log_error("face", "ImportError: face_recognition hoac dlib chua cai.")
            return {"person_name": "Unknown", "confidence": 0.0, "authorized": False}

        # Khởi tạo Liveness Detector (tự động degraded nếu thiếu .dat file)
        liveness_detector: LivenessDetector | None = None
        if self.use_liveness:
            liveness_detector = LivenessDetector(required_blinks=self.required_blinks)
            if not liveness_detector.available():
                logger.warning(
                    "Liveness detection bi tat vi thieu shape_predictor_68_face_landmarks.dat. "
                    "Chi dung SVM."
                )
                liveness_detector = None

        cap = _cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            logger.error("Auth: Khong mo duoc webcam index=%d.", self.camera_index)
            log_error("face", f"Cannot open webcam index={self.camera_index}")
            return {"person_name": "Unknown", "confidence": 0.0, "authorized": False}

        consistent_count = 0
        last_person: str | None = None
        last_result: dict[str, Any] = {"person_name": "Unknown", "confidence": 0.0, "authorized": False}
        deadline = time.time() + self.timeout_seconds

        try:
            while time.time() < deadline:
                ret, frame = cap.read()
                if not ret or frame is None:
                    logger.warning("Auth camera: khong doc duoc frame.")
                    continue

                # ── Giao cho face_module xử lý frame ─────────────────────
                result = process_frame_with_liveness(
                    frame=frame,
                    liveness_detector=liveness_detector,
                    display=self.display,
                )

                # ── Hiển thị thêm thông tin timeout lên cửa sổ ───────────
                if self.display:
                    remaining = max(0.0, deadline - time.time())
                    _cv2.putText(
                        frame,
                        f"Timeout: {remaining:.1f}s  Frames: {consistent_count}/{self.required_frames}",
                        (10, 25),
                        _cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, _cv2.LINE_AA,
                    )
                    _cv2.imshow(self.window_title, frame)
                    if _cv2.waitKey(1) & 0xFF == ord("q"):
                        logger.info("Auth camera: nguoi dung nhan 'q', thoat.")
                        break

                # ── Đếm frame nhất quán ───────────────────────────────────
                # Luôn ghi nhận kết quả cuối cùng nếu có khuôn mặt (để log chính xác người lạ)
                if result.get("face_found"):
                    last_result = {
                        "person_name": result["person_name"],
                        "confidence":  result["confidence"],
                        "authorized":  result["authorized"],
                    }

                if result.get("authorized"):
                    person = result["person_name"]
                    if person == last_person:
                        consistent_count += 1
                    else:
                        if last_person is not None and liveness_detector:
                            # Chỉ reset chớp mắt nếu đổi TỪ người A SANG người B
                            liveness_detector.reset()
                        
                        consistent_count = 1
                        last_person = person

                    if consistent_count >= self.required_frames:
                        logger.info(
                            "Auth: xac thuc thanh cong | person=%s | frames=%d",
                            last_person, self.required_frames,
                        )
                        return last_result
                else:
                    consistent_count = 0
                    last_person = None

        except Exception as exc:
            logger.error("Auth camera loop loi: %s", exc)
            log_error("face", f"Camera loop error: {exc}")
        finally:
            cap.release()
            if self.display:
                _cv2.destroyWindow(self.window_title)

        logger.warning("Auth: timeout sau %.1fs. Tra ve ket qua cuoi cung thay vi mac dinh.", self.timeout_seconds)
        return last_result


    def _execute_command(self, command_data: dict[str, Any]) -> bool:
        """Gọi CommandService để thực thi lệnh đã được authorized."""
        if self.command_service is None:
            logger.error(
                "AuthService không có CommandService. "
                "Inject command_service trước khi gọi authenticate_and_execute()."
            )
            return False

        try:
            return self.command_service.execute_authorized_command(command_data)
        except Exception as exc:
            logger.error("execute_authorized_command thất bại: %s", exc)
            log_error("face", f"execute_authorized_command error: {exc}")
            return False

    @staticmethod
    def _log_face_event(
        person_name: str,
        confidence: float,
        status: str,
        command_id: int | None,
        triggered_by: str,
        device: str,
        room: str,
    ) -> None:
        """Ghi sự kiện xác thực vào bảng face_log."""
        try:
            log_face(
                person_name=person_name,
                confidence=confidence,
                status=status,
                command_id=command_id,
                triggered_by=triggered_by,
                device=device if device else None,
                room=room if room else None,
            )
        except Exception as exc:
            logger.error("Ghi face_log thất bại: %s", exc)

    @staticmethod
    def _update_face_log_action(
        person_name: str,
        confidence: float,
        command_id: int | None,
        action_result: str,
        device: str,
        room: str,
    ) -> None:
        """Ghi lại kết quả thực thi vào face_log (action_result)."""
        try:
            log_face(
                person_name=person_name,
                confidence=confidence,
                status="authorized",
                command_id=command_id,
                triggered_by="post_execute",
                device=device if device else None,
                room=room if room else None,
                action_result=action_result,
            )
        except Exception as exc:
            logger.error("Cập nhật face_log action_result thất bại: %s", exc)
