"""
AuthService - cổng xác thực khuôn mặt cho các hành động nhạy cảm.

OWNER: Face module.

Xem hợp đồng đầy đủ: docs/Integration-Contracts.md (Contract B).

VỊ TRÍ TRONG KIẾN TRÚC
----------------------
Cờ face_auth=True chỉ ĐỊNH TUYẾN lệnh sang đây, nó không tự chặn gì cả.
CommandService.execute_authorized_command() cũng KHÔNG kiểm tra lại face_auth -
nó chạy phần cứng rồi trả bool. Vì vậy:

    LỚP NÀY LÀ CHỐT CHẶN DUY NHẤT Ở THỜI ĐIỂM THỰC THI.

Gọi execute_authorized_command() ngoài nhánh authorized=True là mở cửa cho
người lạ. Không có lớp nào phía sau bắt lỗi giúp.

NGUYÊN TẮC: FAIL CLOSED
-----------------------
Thiếu module, camera lỗi, model ném exception, confidence dưới ngưỡng, hết
thời gian - tất cả đều dẫn tới TỪ CHỐI. Không có nhánh nào "vì không chắc nên
cho qua". Chi phí của một lần từ chối nhầm là người dùng quét lại; chi phí của
một lần cho qua nhầm là cửa nhà mở.

NGƯỠNG LÀ QUYẾT ĐỊNH CỦA SERVER
-------------------------------
Face module trả person_name + confidence. Nếu nó có trả thêm cờ authorized thì
đó chỉ là THAM KHẢO - lớp này tự tính lại từ FACE_AUTH_THRESHOLD. Lý do giống
hệt cách hệ thống đối xử với LLM: một thành phần xác suất được phép báo cáo nó
nhận thấy gì, nhưng cái biến nhận thức thành quyền hạn là cấu hình phía server.
"""

from __future__ import annotations

from typing import Any

from config import settings


# Tên KHÔNG BAO GIỜ được coi là danh tính hợp lệ, kể cả khi confidence rất cao.
#
# CỐ Ý TRÙNG với SvmFaceRecognizer.REJECT_LABELS. Đây là phòng thủ nhiều lớp,
# không phải thừa: face module có thể bị thay bằng implementation khác (mock,
# model mới, thư viện khác) mà quên mất luật này. Lớp cuối cùng trước khi phần
# cứng chạy thì không nên tin vào thiện chí của lớp trước.
REJECT_NAMES = {"unknown", "stranger", "other", "nguoi_la", "nguoi la"}

# Câu trả lời cho người dùng. Gom một chỗ để không lệch nhau giữa các nhánh.
MSG_NO_MODULE = "Từ chối truy cập: hệ thống xác thực khuôn mặt chưa sẵn sàng."
MSG_NO_FRAME = "Từ chối truy cập: không lấy được hình ảnh từ camera."
MSG_ERROR = "Xác thực khuôn mặt gặp sự cố. Vì an toàn, lệnh đã bị hủy."
MSG_DENIED = "Từ chối truy cập. Xác thực danh tính thất bại."
MSG_HARDWARE_FAILED = "Xác thực thành công nhưng thiết bị không phản hồi."
MSG_BAD_HANDOFF = "Không có lệnh hợp lệ để xác thực."


class AuthService:
    """
    Nhận bàn giao từ CommandService (next_step="auth_required"), xác thực khuôn
    mặt, và CHỈ khi xác thực thành công mới cho phép chạy phần cứng.

    Trách nhiệm đầy đủ theo Contract B - Step 3: lớp này cũng phải ĐÓNG LOG.
    handle_transcript() để lại một dòng command_log ở trạng thái waiting_auth
    và không ghi face_log nào. Không đóng thì dòng đó kẹt vĩnh viễn ở
    waiting_auth, latency_ms rỗng, và dashboard đếm sai.
    """

    def __init__(
        self,
        face_recognizer: Any,
        command_service: Any,
        threshold: float | None = None,
        logging_service: Any = None,
    ) -> None:
        """
        Args:
            face_recognizer: đối tượng có recognize(frame) -> dict, theo
                FaceRecognizer (modules/face_recognition/face_module.py).
                Cho phép None: khi đó mọi lệnh đều bị từ chối - fail closed.
            command_service: đối tượng có execute_authorized_command(command)
                -> bool. CHỈ được gọi khi authorized=True.
            threshold: ngưỡng confidence. None -> lấy FACE_AUTH_THRESHOLD.
            logging_service: đối tượng có log_face() và update_command_result().
                None -> dùng hàm cấp module trong services.logging_service.
                Tham số này tồn tại để test chạy được mà không cần database.
        """
        self.face_recognizer = face_recognizer
        self.command_service = command_service
        self.threshold = (
            settings.FACE_AUTH_THRESHOLD if threshold is None else float(threshold)
        )
        self.logging_service = logging_service

    # =========================================================================
    # Điểm vào duy nhất
    # =========================================================================

    def authorize_and_execute(
        self,
        result: dict[str, Any],
        frame: Any = None,
    ) -> dict[str, Any]:
        """
        Args:
            result: nguyên vẹn dict mà CommandService.handle_transcript() trả
                về với next_step="auth_required". KHÔNG dựng lại command từ
                đầu - nó đã được enforce_policy() xử lý phía server rồi.
            frame: khung hình do orchestrator cung cấp (Contract B: System
                owner sở hữu camera). None nghĩa là không lấy được ảnh, và
                điều đó phải dẫn tới TỪ CHỐI, không phải bỏ qua xác thực.

        Returns:
            {
                "authorized": bool,
                "executed": bool,
                "person_name": str | None,
                "confidence": float,
                "status": "authorized" | "denied" | "no_face",
                "response": str,        # câu trả lời cho người dùng
            }
        """
        command = result.get("command") or {}
        command_id = int(result.get("command_id") or -1)

        if not command.get("device") or not command.get("action"):
            # Bàn giao hỏng. Không biết phải chạy gì thì tuyệt đối không đoán.
            return self._deny(
                command_id, command, status="no_face", message=MSG_BAD_HANDOFF
            )

        if self.face_recognizer is None:
            return self._deny(
                command_id, command, status="no_face", message=MSG_NO_MODULE
            )

        if frame is None:
            return self._deny(
                command_id, command, status="no_face", message=MSG_NO_FRAME
            )

        # Chụp TRƯỚC khi nhận diện, để cả trường hợp recognize() ném lỗi vẫn
        # còn bằng chứng ảnh. Đây là chỗ duy nhất có frame, nên cũng là chỗ
        # duy nhất lưu được.
        snapshot = None
        try:
            from modules.face_recognition.face_module import save_snapshot
            snapshot = save_snapshot(frame)
        except Exception as exc:
            self._log_error("face", f"save_snapshot failed: {exc}")

        try:
            face_result = self.face_recognizer.recognize(frame) or {}
        except Exception as exc:
            self._log_error("face", f"recognize failed: {exc}")
            return self._deny(
                command_id, command, status="no_face", message=MSG_ERROR,
                snapshot_path=snapshot,
            )

        person_name = self._clean_name(face_result.get("person_name"))
        confidence = self._clean_confidence(face_result.get("confidence"))

        # Contract B - Step 2. Cố ý KHÔNG đọc face_result["authorized"] nếu có.
        authorized = person_name is not None and confidence >= self.threshold

        if not authorized:
            status = "no_face" if person_name is None and confidence <= 0.0 else "denied"
            return self._deny(
                command_id,
                command,
                status=status,
                message=MSG_DENIED,
                person_name=person_name,
                confidence=confidence,
                snapshot_path=snapshot,
            )

        # ĐÃ XÁC THỰC. Đây là chỗ DUY NHẤT trong toàn hệ thống được phép gọi
        # execute_authorized_command() cho một lệnh cần face_auth.
        try:
            executed = bool(
                self.command_service.execute_authorized_command(command)
            )
        except Exception as exc:
            self._log_error("gateway", f"execute_authorized_command failed: {exc}")
            executed = False

        self._close_log(
            command_id,
            command=command,
            status="authorized",
            executed=executed,
            person_name=person_name,
            confidence=confidence,
            snapshot_path=snapshot,
        )

        if not executed:
            response = MSG_HARDWARE_FAILED
        else:
            response = (
                result.get("response")
                or command.get("response")
                or "Đã thực hiện lệnh."
            )

        return {
            "authorized": True,
            "executed": executed,
            "person_name": person_name,
            "confidence": confidence,
            "status": "authorized",
            "response": response,
        }

    # =========================================================================
    # Chuẩn hoá dữ liệu từ face module
    # =========================================================================

    @staticmethod
    def _clean_name(raw: Any) -> str | None:
        """
        Chuỗi rỗng, khoảng trắng, và các nhãn "người lạ" đều thành None.

        "" là chuỗi falsy nên `bool(person_name)` đã chặn được, nhưng
        "Unknown" thì KHÔNG - nó truthy. Xem REJECT_NAMES ở đầu file.
        """
        if raw is None:
            return None

        name = str(raw).strip()
        if not name or name.lower() in REJECT_NAMES:
            return None

        return name

    @staticmethod
    def _clean_confidence(raw: Any) -> float:
        """
        Ép về float thuần trong [0.0, 1.0].

        Hai lý do:
        - predict_proba() của sklearn trả numpy.float64; để lọt vào SQLite sẽ
          gây "Error binding parameter" đúng lúc có người đứng trước camera.
        - log_face() từ chối ghi nếu confidence ngoài [0.0, 1.0], nên một giá
          trị lệch sẽ làm MẤT dòng audit của chính lần bị từ chối.
        """
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0

        if value != value:  # NaN
            return 0.0

        return min(max(value, 0.0), 1.0)

    # =========================================================================
    # Từ chối + ghi log
    # =========================================================================

    def _deny(
        self,
        command_id: int,
        command: dict[str, Any],
        *,
        status: str,
        message: str,
        person_name: str | None = None,
        confidence: float = 0.0,
        snapshot_path: str | None = None,
    ) -> dict[str, Any]:
        self._close_log(
            command_id,
            command=command,
            status=status,
            executed=False,
            person_name=person_name,
            confidence=confidence,
            snapshot_path=snapshot_path,
        )

        return {
            "authorized": False,
            "executed": False,
            "person_name": person_name,
            "confidence": confidence,
            "status": status,
            "response": message,
        }

    def _close_log(
        self,
        command_id: int,
        *,
        command: dict[str, Any],
        status: str,
        executed: bool,
        person_name: str | None,
        confidence: float,
        snapshot_path: str | None = None,
    ) -> None:
        """
        Đóng command_log và ghi face_log (Contract B - Step 3).

        Lỗi ghi log KHÔNG được làm hỏng quyết định an ninh: nếu database khoá
        hoặc đầy đĩa, cửa vẫn phải mở cho người đã xác thực đúng, và vẫn phải
        đóng với người lạ. Vì vậy mọi lời gọi ở đây đều bọc try/except.

        status hợp lệ theo schema: authorized | denied | no_face | timeout
        """
        if command_id <= 0:
            return

        authorized = status == "authorized"

        if authorized and executed:
            result_status, execution_status = "success", "success"
        elif authorized:
            result_status, execution_status = "fail: hardware_error", "failed"
        else:
            result_status, execution_status = "rejected: face_auth", "rejected"

        update_fn, face_fn = self._log_functions()

        try:
            update_fn(
                command_id=command_id,
                result=result_status,
                execution_status=execution_status,
                error_message=None if execution_status == "success" else result_status,
            )
        except Exception as exc:
            self._log_error("gateway", f"update_command_result failed: {exc}")

        try:
            face_fn(
                # face_log.person_name là NOT NULL. Người lạ vẫn phải có một
                # dòng audit, nếu không thì mọi lần từ chối đều biến mất khỏi
                # lịch sử - đúng thứ cần nhất khi điều tra sự cố.
                person_name=person_name or "unknown",
                confidence=confidence,
                status=status,
                command_id=command_id,
                triggered_by="voice_command",
                device=command.get("device"),
                room=command.get("room"),
                action_result=execution_status,
                snapshot_path=snapshot_path,
            )
        except Exception as exc:
            self._log_error("gateway", f"log_face failed: {exc}")

    def _log_functions(self):
        """Trả (update_command_result, log_face) - từ DI hoặc từ module thật."""
        if self.logging_service is not None:
            return (
                self.logging_service.update_command_result,
                self.logging_service.log_face,
            )

        from services.logging_service import log_face, update_command_result

        return update_command_result, log_face

    def _log_error(self, tag: str, message: str) -> None:
        try:
            if self.logging_service is not None and hasattr(
                self.logging_service, "log_error"
            ):
                self.logging_service.log_error(tag, message)
                return

            from services.logging_service import log_error

            log_error(tag, message)
        except Exception:
            # Ghi log lỗi mà cũng lỗi thì im lặng. Không được để việc ghi log
            # làm sập luồng xác thực.
            pass


__all__ = ["AuthService", "REJECT_NAMES"]
