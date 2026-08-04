from __future__ import annotations

import time
from typing import Any

from config.settings import SESSION_MAX_TURNS, SESSION_TTL_SECONDS


class SessionService:
    """
    Lưu command đang chờ làm rõ của từng phiên hội thoại.

    Vì sao cần:
        User : bật đèn
        Bot  : Bạn muốn điều khiển đèn ở phòng nào?
        User : phòng khách
        Bot  : ???

    Không có chỗ nhớ, bot hỏi xong quên sạch và hỏi lại từ đầu - clarify
    trở thành vòng lặp vô tận.

    Thiết kế:
    - Lưu trong bộ nhớ (đủ cho một tiến trình Flask). Nếu sau này chạy nhiều
      worker, thay bằng Redis hoặc một bảng SQLite - interface giữ nguyên.
    - Có TTL: hội thoại bỏ dở không được sống mãi. Nửa tiếng sau user nói
      "phòng khách" thì đó là câu nói mới, không phải trả lời câu hỏi cũ.
    - Có giới hạn số lượt: chặn vòng lặp clarify vô tận khi parser cứ hỏi mãi.
    """

    def __init__(
        self,
        ttl_seconds: float | None = None,
        max_turns: int | None = None,
    ) -> None:
        self.ttl_seconds = (
            SESSION_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        )
        self.max_turns = SESSION_MAX_TURNS if max_turns is None else max_turns

        # session_id -> {"command": dict, "updated_at": float, "turns": int}
        self._sessions: dict[str, dict[str, Any]] = {}

        # session_id -> {"payload": dict, "updated_at": float}
        #
        # TÁCH RIÊNG khỏi _sessions có chủ đích. Yêu cầu đăng ký đang chờ người
        # dùng trả lời Có/Không là một trạng thái KHÁC với câu lệnh đang chờ
        # làm rõ: nó không được tính vào SESSION_MAX_TURNS.
        #
        # Dữ liệu thật cho thấy vì sao: cả hai lần chạm giới hạn lượt đều do
        # câu đồng ý, không phải do câu lệnh mơ hồ.
        #     (1892, 'ok', 'failed', 'fail: clarify_limit')
        #     (1904, 'ok', 'failed', 'fail: clarify_limit')
        self._registries: dict[str, dict[str, Any]] = {}

    # =========================================================================
    # Read
    # =========================================================================

    def get_pending(self, session_id: str | None) -> dict[str, Any] | None:
        """
        Trả về command đang chờ làm rõ của phiên này.

        None nếu không có, đã hết hạn, hoặc đã hỏi quá nhiều lượt.
        """
        if not session_id:
            return None

        entry = self._sessions.get(session_id)
        if entry is None:
            return None

        if self._is_expired(entry):
            self.clear(session_id)
            return None

        return dict(entry["command"])

    def get_turns(self, session_id: str | None) -> int:
        """Đã hỏi lại bao nhiêu lượt trong phiên này."""
        if not session_id:
            return 0

        entry = self._sessions.get(session_id)
        if entry is None or self._is_expired(entry):
            return 0

        return int(entry["turns"])

    def has_reached_turn_limit(self, session_id: str | None) -> bool:
        """Đã hỏi tới giới hạn chưa? Nếu rồi thì phải dừng, đừng hỏi nữa."""
        return self.get_turns(session_id) >= self.max_turns

    # =========================================================================
    # Write
    # =========================================================================

    def set_pending(
        self,
        session_id: str | None,
        command: dict[str, Any],
    ) -> None:
        """Lưu command đang chờ làm rõ và tăng số lượt đã hỏi."""
        if not session_id:
            return

        entry = self._sessions.get(session_id)
        turns = 0

        if entry is not None and not self._is_expired(entry):
            turns = int(entry["turns"])

        self._sessions[session_id] = {
            "command": dict(command),
            "updated_at": time.time(),
            "turns": turns + 1,
        }

    def touch(self, session_id: str | None) -> None:
        """
        Làm mới TTL mà KHÔNG tăng số lượt.

        Dùng khi người dùng vừa trả lời Có/Không cho một yêu cầu đăng ký:
        hội thoại vẫn đang sống, nhưng lượt đó không phải một lần hỏi lại nên
        không được tiêu vào SESSION_MAX_TURNS.
        """
        if not session_id:
            return

        entry = self._sessions.get(session_id)
        if entry is not None and not self._is_expired(entry):
            entry["updated_at"] = time.time()

    def clear(self, session_id: str | None) -> None:
        """Xoá phiên. Gọi khi lệnh đã hoàn tất hoặc bị từ chối."""
        if session_id:
            self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        self._sessions.clear()
        self._registries.clear()

    # =========================================================================
    # Yêu cầu đăng ký đang chờ xác nhận
    # =========================================================================

    def set_pending_registry(
        self,
        session_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        """
        Ghi nhớ yêu cầu đăng ký vừa hỏi người dùng, để lượt sau xử lý câu
        trả lời Có/Không.

        payload nên chứa: room, device, action, command_id - đủ để cập nhật
        đúng dòng command_log khi người dùng quyết định.
        """
        if not session_id:
            return

        self._registries[session_id] = {
            "payload": dict(payload),
            "updated_at": time.time(),
        }

    def get_pending_registry(self, session_id: str | None) -> dict[str, Any] | None:
        """Yêu cầu đăng ký đang chờ xác nhận, hoặc None nếu không có/hết hạn."""
        if not session_id:
            return None

        entry = self._registries.get(session_id)
        if entry is None:
            return None

        if self._is_expired(entry):
            self.clear_registry(session_id)
            return None

        return dict(entry["payload"])

    def clear_registry(self, session_id: str | None) -> None:
        """Xoá yêu cầu đăng ký đang chờ. Gọi sau khi người dùng đã trả lời."""
        if session_id:
            self._registries.pop(session_id, None)

    # =========================================================================
    # Internal
    # =========================================================================

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        if self.ttl_seconds <= 0:
            return False

        return (time.time() - float(entry["updated_at"])) > self.ttl_seconds