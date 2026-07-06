from __future__ import annotations

import sqlite3
from typing import Any

from config.settings import DB_PATH
from services.logging_service import log_error


class RuleService:
    """
    Automation Rules Engine.

    Responsibilities:
    - Create automation rules from create_rule commands.
    - Store rules in SQLite.
    - Return active rules.
    - Evaluate current sensor data and return triggered device actions.
    """

    def __init__(self) -> None:
        self._ensure_rules_table()

    def _ensure_rules_table(self) -> None:
        """Ensure automation_rules table exists."""
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)

            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automation_rules (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        command_id  INTEGER,
                        sensor      TEXT    NOT NULL,
                        operator    TEXT    NOT NULL,
                        value       REAL    NOT NULL,
                        action      TEXT    NOT NULL,
                        device      TEXT    NOT NULL,
                        room        TEXT    NOT NULL,
                        is_active   INTEGER NOT NULL DEFAULT 1,
                        created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                        FOREIGN KEY (command_id) REFERENCES command_log(id)
                    )
                    """
                )
                conn.commit()

        except Exception as exc:
            log_error("rule_service", f"Failed to initialize automation_rules table: {exc}")

    def create_rule(
        self,
        command_id: int | None,
        command: dict[str, Any],
    ) -> int:
        """
        Create an automation rule from a validated create_rule command.

        Returns:
            rule_id if success, otherwise -1.
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

        if not sensor or not operator or value is None or not action or not device or not room:
            return -1

        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO automation_rules (
                        command_id,
                        sensor,
                        operator,
                        value,
                        action,
                        device,
                        room,
                        is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        command_id,
                        sensor,
                        operator,
                        float(value),
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

    def get_active_rules(self) -> list[dict[str, Any]]:
        """Return all active automation rules."""
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
        """Deactivate one automation rule."""
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute(
                    """
                    UPDATE automation_rules
                    SET is_active = 0
                    WHERE id = ?
                    """,
                    (rule_id,),
                )
                conn.commit()

        except Exception as exc:
            log_error("rule_service", f"Failed to deactivate automation rule: {exc}")

    def evaluate_sensor_data(
        self,
        current_sensor_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Evaluate sensor values against active rules.

        Returns a list of device actions that should be executed.
        """
        triggered_actions: list[dict[str, Any]] = []
        active_rules = self.get_active_rules()

        for rule in active_rules:
            sensor_name = rule["sensor"]

            if sensor_name not in current_sensor_data:
                continue

            try:
                current_value = float(current_sensor_data[sensor_name])
                threshold_value = float(rule["value"])
            except (TypeError, ValueError):
                continue

            if self._compare(
                current_value=current_value,
                operator=rule["operator"],
                threshold_value=threshold_value,
            ):
                triggered_actions.append(
                    {
                        "rule_id": rule["id"],
                        "action": rule["action"],
                        "device": rule["device"],
                        "room": rule["room"],
                        "response": (
                            f"Kích hoạt tự động {rule['device']} tại {rule['room']} "
                            f"do {sensor_name} đạt {current_value}."
                        ),
                    }
                )

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