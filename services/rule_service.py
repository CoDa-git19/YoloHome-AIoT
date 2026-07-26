from __future__ import annotations

import sqlite3
from typing import Any

from config.settings import DB_PATH, RULE_HYSTERESIS
from database.init_db import init_db
from services.logging_service import log_error


class RuleService:
    """
    Automation Rules Engine.

    Trách nhiệm:
    - Tạo automation rule từ command create_rule.
    - Lưu rule vào SQLite.
    - Đánh giá dữ liệu cảm biến và trả về các hành động cần thực thi.

    EDGE-TRIGGERED
    --------------
    Rule chỉ kích hoạt khi điều kiện CHUYỂN từ sai sang đúng, không phải mỗi
    lần điều kiện đang đúng.

    Nếu không, rule "nhiệt độ > 30 thì bật quạt" sẽ gửi lệnh turn_on xuống
    Yolo:Bit ở MỌI vòng đọc cảm biến suốt cả ngày nóng - spam phần cứng và
    làm ngập command_log.

    HYSTERESIS
    ----------
    Điều kiện chỉ được "nạp lại" khi giá trị rơi ra khỏi ngưỡng một khoảng
    RULE_HYSTERESIS. Tránh việc nhiệt độ dao động 29.9/30.1 làm quạt bật tắt
    xoành xoạch.
    """

    def __init__(self, hysteresis: float | None = None) -> None:
        self.hysteresis = RULE_HYSTERESIS if hysteresis is None else hysteresis
        self._ensure_schema()

    # =========================================================================
    # Schema
    # =========================================================================

    def _ensure_schema(self) -> None:
        """
        Đảm bảo bảng tồn tại.

        Dùng lại database/schema.sql làm NGUỒN SỰ THẬT DUY NHẤT, thay vì
        định nghĩa lại CREATE TABLE ở đây (trước kia schema bị khai báo
        2 nơi và rất dễ lệch nhau).
        """
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            init_db()
            self._migrate()
        except Exception as exc:
            log_error("rule_service", f"Failed to initialize schema: {exc}")

    def _migrate(self) -> None:
        """Thêm cột mới vào automation_rules cho các database đã tồn tại."""
        new_columns = {
            "last_state": "INTEGER NOT NULL DEFAULT 0",
            "last_triggered_at": "TEXT",
        }

        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                existing = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(automation_rules)")
                }

                for column, definition in new_columns.items():
                    if column not in existing:
                        conn.execute(
                            f"ALTER TABLE automation_rules ADD COLUMN {column} {definition}"
                        )

                conn.commit()

        except Exception as exc:
            log_error("rule_service", f"Failed to migrate automation_rules: {exc}")

    # =========================================================================
    # Create
    # =========================================================================

    def create_rule(
        self,
        command_id: int | None,
        command: dict[str, Any],
    ) -> int:
        """
        Tạo automation rule từ một create_rule command đã validate.

        Nếu rule y hệt đã tồn tại và đang active, trả về id của rule cũ
        thay vì tạo bản sao.

        Returns:
            rule_id nếu thành công, ngược lại -1.
        """
        condition = command.get("condition")

        if not isinstance(condition, dict):
            return -1

        sensor = condition.get("sensor")
        operator = condition.get("operator")
        value = condition.get("value")

        action = command.get("action")
        device = command.get("device")
        room = command.get("room")

        if (
            not sensor
            or not operator
            or value is None
            or not action
            or not device
            or not room
        ):
            return -1

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return -1

        existing = self.find_rule(
            sensor=sensor,
            operator=operator,
            value=numeric_value,
            action=action,
            device=device,
            room=room,
        )
        if existing is not None:
            return int(existing["id"])

        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO automation_rules (
                        command_id, sensor, operator, value,
                        action, device, room, is_active, last_state
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)
                    """,
                    (
                        command_id,
                        sensor,
                        operator,
                        numeric_value,
                        action,
                        device,
                        room,
                    ),
                )
                conn.commit()
                return int(cursor.lastrowid)

        except Exception as exc:
            log_error("rule_service", f"Failed to save automation rule: {exc}")
            return -1

    def find_rule(
        self,
        sensor: str,
        operator: str,
        value: float,
        action: str,
        device: str,
        room: str,
    ) -> dict[str, Any] | None:
        """Tìm rule active y hệt, nếu có."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT *
                    FROM automation_rules
                    WHERE is_active = 1
                      AND sensor = ? AND operator = ? AND value = ?
                      AND action = ? AND device = ? AND room = ?
                    LIMIT 1
                    """,
                    (sensor, operator, value, action, device, room),
                ).fetchone()

                return dict(row) if row else None

        except Exception as exc:
            log_error("rule_service", f"Failed to look up automation rule: {exc}")
            return None

    # =========================================================================
    # Read / update
    # =========================================================================

    def get_active_rules(self) -> list[dict[str, Any]]:
        """Trả về mọi automation rule đang active."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT *
                    FROM automation_rules
                    WHERE is_active = 1
                    ORDER BY id ASC
                    """
                ).fetchall()

                return [dict(row) for row in rows]

        except Exception as exc:
            log_error("rule_service", f"Failed to retrieve automation rules: {exc}")
            return []

    def deactivate_rule(self, rule_id: int) -> None:
        """Tắt một automation rule."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute(
                    "UPDATE automation_rules SET is_active = 0 WHERE id = ?",
                    (rule_id,),
                )
                conn.commit()

        except Exception as exc:
            log_error("rule_service", f"Failed to deactivate automation rule: {exc}")

    def _set_rule_state(self, rule_id: int, state: bool, triggered: bool) -> None:
        """Ghi lại điều kiện của rule đang đúng hay sai ở lần đọc này."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                if triggered:
                    conn.execute(
                        """
                        UPDATE automation_rules
                        SET last_state = ?,
                            last_triggered_at = datetime('now', 'localtime')
                        WHERE id = ?
                        """,
                        (1 if state else 0, rule_id),
                    )
                else:
                    conn.execute(
                        "UPDATE automation_rules SET last_state = ? WHERE id = ?",
                        (1 if state else 0, rule_id),
                    )

                conn.commit()

        except Exception as exc:
            log_error("rule_service", f"Failed to update rule state: {exc}")

    # =========================================================================
    # Evaluate
    # =========================================================================

    def evaluate_sensor_data(
        self,
        current_sensor_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Đánh giá giá trị cảm biến với các rule đang active.

        Chỉ trả về hành động khi điều kiện CHUYỂN từ sai sang đúng.
        Điều kiện đang đúng liên tục sẽ không kích hoạt lại.
        """
        triggered_actions: list[dict[str, Any]] = []

        for rule in self.get_active_rules():
            sensor_name = rule["sensor"]

            if sensor_name not in current_sensor_data:
                continue

            try:
                current_value = float(current_sensor_data[sensor_name])
                threshold_value = float(rule["value"])
            except (TypeError, ValueError):
                continue

            operator = rule["operator"]
            rule_id = int(rule["id"])

            condition_now = self._compare(
                current_value=current_value,
                operator=operator,
                threshold_value=threshold_value,
            )
            condition_before = bool(rule.get("last_state", 0))

            # Cạnh lên: sai -> đúng. Đây là lúc DUY NHẤT được kích hoạt.
            if condition_now and not condition_before:
                self._set_rule_state(rule_id, True, triggered=True)

                triggered_actions.append(
                    {
                        "rule_id": rule_id,
                        "action": rule["action"],
                        "device": rule["device"],
                        "room": rule["room"],
                        "response": (
                            f"Kích hoạt tự động {rule['device']} tại {rule['room']} "
                            f"do {sensor_name} đạt {current_value}."
                        ),
                    }
                )
                continue

            # Cạnh xuống: chỉ nạp lại rule khi giá trị đã ra khỏi ngưỡng
            # một khoảng hysteresis, tránh bật/tắt xoành xoạch quanh ngưỡng.
            if condition_before and not condition_now:
                if self._clearly_false(
                    current_value=current_value,
                    operator=operator,
                    threshold_value=threshold_value,
                ):
                    self._set_rule_state(rule_id, False, triggered=False)

        return triggered_actions

    @staticmethod
    def _compare(
        current_value: float,
        operator: str,
        threshold_value: float,
    ) -> bool:
        if operator == ">":
            return current_value > threshold_value

        if operator == "<":
            return current_value < threshold_value

        if operator == ">=":
            return current_value >= threshold_value

        if operator == "<=":
            return current_value <= threshold_value

        if operator == "==":
            return current_value == threshold_value

        return False

    def _clearly_false(
        self,
        current_value: float,
        operator: str,
        threshold_value: float,
    ) -> bool:
        """
        Điều kiện đã sai một cách RÕ RÀNG chưa (ra khỏi vùng chết)?

        Chỉ khi đó rule mới được nạp lại để có thể kích hoạt lần sau.
        """
        margin = self.hysteresis

        if margin <= 0:
            return True

        if operator in {">", ">="}:
            return current_value < threshold_value - margin

        if operator in {"<", "<="}:
            return current_value > threshold_value + margin

        if operator == "==":
            return abs(current_value - threshold_value) > margin

        return True