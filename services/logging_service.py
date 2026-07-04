import json
import sqlite3
import sys
from typing import Any, Dict

from config.settings import DB_PATH

_VALID_FACE_STATUS = {"authorized", "denied", "no_face", "timeout"}


class LoggingService:
    def update(self, sensor_data: Dict[str, Any]) -> None:
        self.log_sensor_data(sensor_data)

    def log_sensor_data(self, sensor_data: Dict[str, Any]) -> None:
        # TODO: persist sensor readings to a sensor_log table when defined
        print(f"[logging_service] sensor data: {sensor_data}")

    def log_error(self, module: str, message: str) -> None:
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute(
                    "INSERT INTO error_log (module, message) VALUES (?, ?)",
                    (module, message),
                )
        except Exception as exc:
            print(f"[logging_service] log_error failed: {exc}", file=sys.stderr)

    def log_command(
        self,
        transcript: str,
        json_cmd: dict,
        result: str,
        validation_status: str | None = None,
        execution_status: str | None = None,
        latency_ms: int | None = None,
        error_message: str | None = None,
    ) -> int:
        intent = json_cmd.get("intent")
        action = json_cmd.get("action")
        device = json_cmd.get("device")
        room = json_cmd.get("room")
        face_auth = int(json_cmd.get("face_auth", False))

        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO command_log
                        (transcript, json_cmd, intent, action, device, room,
                         face_auth, validation_status, execution_status,
                         result, latency_ms, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transcript,
                        json.dumps(json_cmd, ensure_ascii=False),
                        intent,
                        action,
                        device,
                        room,
                        face_auth,
                        validation_status,
                        execution_status,
                        result,
                        latency_ms,
                        error_message,
                    ),
                )
                return cur.lastrowid
        except Exception as exc:
            self.log_error("db", f"log_command failed: {exc}")
            raise

    def log_face(
        self,
        person_name: str,
        confidence: float,
        status: str,
        command_id: int | None = None,
        triggered_by: str | None = None,
        device: str | None = None,
        room: str | None = None,
        action_result: str | None = None,
    ) -> None:
        if not (0.0 <= confidence <= 1.0):
            self.log_error("db", f"log_face: confidence {confidence} out of range [0.0, 1.0]")
            return
        if status not in _VALID_FACE_STATUS:
            self.log_error("db", f"log_face: invalid status '{status}'")
            return
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute(
                    """
                    INSERT INTO face_log
                        (command_id, person_name, confidence, status, triggered_by, device, room, action_result)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (command_id, person_name, confidence, status, triggered_by, device, room, action_result),
                )
        except Exception as exc:
            self.log_error("db", f"log_face failed: {exc}")

    def update_command_result(
        self,
        command_id: int,
        result: str,
        execution_status: str | None = None,
        error_message: str | None = None,
    ) -> None:
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute(
                    """
                    UPDATE command_log
                    SET result = ?,
                        execution_status = COALESCE(?, execution_status),
                        error_message    = COALESCE(?, error_message)
                    WHERE id = ?
                    """,
                    (result, execution_status, error_message, command_id),
                )
        except Exception as exc:
            self.log_error("db", f"update_command_result failed: {exc}")

    def add_schedule(self, run_at: str, json_cmd: dict) -> int:
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                cur = conn.execute(
                    "INSERT INTO schedule (run_at, json_cmd) VALUES (?, ?)",
                    (run_at, json.dumps(json_cmd, ensure_ascii=False)),
                )
                return cur.lastrowid
        except Exception as exc:
            self.log_error("db", f"add_schedule failed: {exc}")
            raise

    def get_schedules(self) -> list[dict]:
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    """
                    SELECT * FROM schedule
                    WHERE is_active = 1
                      AND run_at <= datetime('now', 'localtime')
                    """,
                )
                rows = cur.fetchall()
                results = [dict(row) for row in rows]

                if results:
                    ids = [r["id"] for r in results]
                    conn.execute(
                        f"UPDATE schedule SET is_active = 0 WHERE id IN ({','.join('?' * len(ids))})",
                        ids,
                    )

                return results
        except Exception as exc:
            self.log_error("db", f"get_schedules failed: {exc}")
            return []

_svc = LoggingService()

log_error = _svc.log_error
log_command = _svc.log_command
log_face = _svc.log_face
update_command_result = _svc.update_command_result
add_schedule = _svc.add_schedule
get_schedules = _svc.get_schedules
