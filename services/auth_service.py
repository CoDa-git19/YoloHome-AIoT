"""
Auth service: cổng xác thực khuôn mặt cho các lệnh nhạy cảm.

OWNER: Face module.

Đây là ĐIỂM THỰC THI DUY NHẤT của chính sách bảo mật lúc chạy. Cờ
face_auth=True mà LLM/validator trả về chỉ ĐỊNH TUYẾN lệnh tới đây; nó
KHÔNG chặn phần cứng. CommandService.execute_authorized_command() tin
tưởng hoàn toàn caller đã xác thực - nên tuyệt đối không gọi nó khi
xác thực chưa qua.

Phần glue (so ngưỡng, thực thi, ghi log) đã hoàn chỉnh ở đây. Face owner
chỉ cần cung cấp một FaceRecognizer thật (mặc định dùng MockFaceRecognizer).

Xem hợp đồng đầy đủ: docs/Integration-Contracts.md (Contract B).
"""

from __future__ import annotations

from typing import Any

from config.settings import FACE_AUTH_THRESHOLD
from modules.face_recognition.face_module import FaceRecognizer, MockFaceRecognizer
from services.logging_service import log_error, log_face, update_command_result


class AuthService:
    """
    Điều phối luồng xác thực khuôn mặt cho lệnh next_step == "auth_required".

    Cách dùng (trong main.py):
        result = command_service.handle_transcript(transcript, ...)
        if result["next_step"] == "auth_required":
            auth = AuthService(command_service)
            outcome = auth.authorize_and_execute(result, frame=camera_frame)
    """

    def __init__(
        self,
        command_service: Any,
        recognizer: FaceRecognizer | None = None,
        threshold: float | None = None,
    ) -> None:
        self.command_service = command_service
        # Mặc định dùng mock để pipeline chạy được ngay; thay bằng
        # SvmFaceRecognizer khi Face owner hoàn thiện.
        self.recognizer = recognizer or MockFaceRecognizer()
        self.threshold = (
            threshold if threshold is not None else FACE_AUTH_THRESHOLD
        )

    def authorize_and_execute(
        self,
        pending_result: dict[str, Any],
        frame: Any = None,
    ) -> dict[str, Any]:
        """
        Xác thực khuôn mặt rồi thực thi lệnh nếu qua.

        Args:
            pending_result: nguyên dict mà handle_transcript trả về với
                next_step == "auth_required". Phải chứa "command" và "command_id".
            frame: khung hình camera để nhận diện (None -> recognizer tự xử lý).

        Returns:
            {
                "authorized": bool,
                "executed": bool,
                "person_name": str | None,
                "confidence": float,
                "response": str,      # câu trả lời tiếng Việt cho người dùng
            }
        """
        command = pending_result.get("command") or {}
        command_id = int(pending_result.get("command_id") or -1)
        device = command.get("device")
        room = command.get("room")

        # 1. Nhận diện khuôn mặt (Face module).
        try:
            recognition = self.recognizer.recognize(frame)
        except Exception as exc:
            log_error("auth_service", f"Face recognition failed: {exc}")
            recognition = {"person_name": None, "confidence": 0.0}

        person_name = recognition.get("person_name")
        confidence = float(recognition.get("confidence") or 0.0)

        # 2. Quyết định authorized - CHÍNH SÁCH SERVER, không tin Face module.
        authorized = person_name is not None and confidence >= self.threshold

        # 3. Không qua -> từ chối, ghi log, KHÔNG chạm phần cứng.
        if not authorized:
            face_status = "no_face" if person_name is None else "denied"
            self._log(
                command_id=command_id,
                person_name=person_name or "unknown",
                confidence=confidence,
                face_status=face_status,
                device=device,
                room=room,
                result="rejected: face_auth",
                execution_status="rejected",
            )
            return {
                "authorized": False,
                "executed": False,
                "person_name": person_name,
                "confidence": confidence,
                "response": "Xác thực khuôn mặt không thành công. Không thể mở cửa.",
            }

        # 4. Qua xác thực -> thực thi. Đây là NƠI DUY NHẤT được phép gọi
        #    execute_authorized_command sau khi đã xác thực.
        executed = self.command_service.execute_authorized_command(command)

        self._log(
            command_id=command_id,
            person_name=person_name,
            confidence=confidence,
            face_status="authorized",
            device=device,
            room=room,
            result="success" if executed else "fail: hardware_error",
            execution_status="success" if executed else "failed",
            action_result="success" if executed else "hardware_error",
        )

        if executed:
            response = f"Đã xác thực {person_name}. Đang mở cửa."
        else:
            response = "Đã xác thực nhưng không gửi được lệnh tới phần cứng."

        return {
            "authorized": True,
            "executed": executed,
            "person_name": person_name,
            "confidence": confidence,
            "response": response,
        }

    def _log(
        self,
        command_id: int,
        person_name: str,
        confidence: float,
        face_status: str,
        device: str | None,
        room: str | None,
        result: str,
        execution_status: str,
        action_result: str | None = None,
    ) -> None:
        """
        Đóng vòng đời log mà handle_transcript để lại ở trạng thái waiting_auth.

        execute_authorized_command KHÔNG chạm database, nên việc cập nhật
        command_log + ghi face_log là trách nhiệm của auth_service.
        """
        if command_id > 0:
            update_command_result(
                command_id=command_id,
                result=result,
                execution_status=execution_status,
            )

        # confidence phải nằm trong [0.0, 1.0], nếu không log_face sẽ bỏ qua.
        log_face(
            person_name=person_name,
            confidence=max(0.0, min(1.0, confidence)),
            status=face_status,  # {authorized | denied | no_face | timeout}
            command_id=command_id if command_id > 0 else None,
            triggered_by="voice_command",
            device=device,
            room=room,
            action_result=action_result,
        )


__all__ = ["AuthService"]
