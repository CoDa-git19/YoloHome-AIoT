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

    def clear(self, session_id: str | None) -> None:
        """Xoá phiên. Gọi khi lệnh đã hoàn tất hoặc bị từ chối."""
        if session_id:
            self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        self._sessions.clear()

    # =========================================================================
    # Internal
    # =========================================================================

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        if self.ttl_seconds <= 0:
            return False

        return (time.time() - float(entry["updated_at"])) > self.ttl_seconds