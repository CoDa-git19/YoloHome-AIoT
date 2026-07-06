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
        """
        Persist sensor readings into sensor_log.

        Supported input formats:

        1. Flat readings:
        {
            "temperature": 30,
            "humidity": 55,
            "room": "living_room",
            "source": "mock"
        }

        2. Nested readings:
        {
            "room": "living_room",
            "source": "hardware",
            "readings": {
                "temperature": {"value": 30, "unit": "C"},
                "humidity": {"value": 55, "unit": "%"}
            }
        }

        3. Single reading:
        {
            "sensor": "temperature",
            "value": 30,
            "unit": "C",
            "room": "living_room",
            "source": "mock"
        }
        """
        readings = self._normalize_sensor_readings(sensor_data)

        if not readings:
            return

        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.executemany(
                    """
                    INSERT INTO sensor_log
                        (sensor, value, unit, room, source, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    readings,
                )
        except Exception as exc:
            self.log_error("db", f"log_sensor_data failed: {exc}")

    def _normalize_sensor_readings(
        self,
        sensor_data: Dict[str, Any],
    ) -> list[tuple[str, float | None, str | None, str | None, str | None, str]]:
        if not isinstance(sensor_data, dict):
            return []

        room = self._as_optional_str(sensor_data.get("room"))
        source = self._as_optional_str(sensor_data.get("source", "unknown"))
        raw_json = json.dumps(sensor_data, ensure_ascii=False)

        # Format 3: single reading
        if "sensor" in sensor_data:
            sensor = self._as_optional_str(sensor_data.get("sensor"))
            if not sensor:
                return []

            return [
                (
                    sensor,
                    self._as_optional_float(sensor_data.get("value")),
                    self._as_optional_str(sensor_data.get("unit")),
                    room,
                    source,
                    raw_json,
                )
            ]

        # Format 2: nested readings
        if isinstance(sensor_data.get("readings"), dict):
            rows = []
            for sensor, reading in sensor_data["readings"].items():
                if isinstance(reading, dict):
                    value = reading.get("value")
                    unit = reading.get("unit")
                    reading_room = reading.get("room", room)
                    reading_source = reading.get("source", source)
                else:
                    value = reading
                    unit = None
                    reading_room = room
                    reading_source = source

                rows.append(
                    (
                        str(sensor),
                        self._as_optional_float(value),
                        self._as_optional_str(unit),
                        self._as_optional_str(reading_room),
                        self._as_optional_str(reading_source),
                        raw_json,
                    )
                )

            return rows

        # Format 1: flat readings
        metadata_keys = {"room", "source", "unit", "timestamp", "raw_json"}
        rows = []

        for sensor, value in sensor_data.items():
            if sensor in metadata_keys:
                continue

            if isinstance(value, dict):
                rows.append(
                    (
                        str(sensor),
                        self._as_optional_float(value.get("value")),
                        self._as_optional_str(value.get("unit")),
                        self._as_optional_str(value.get("room", room)),
                        self._as_optional_str(value.get("source", source)),
                        raw_json,
                    )
                )
            else:
                rows.append(
                    (
                        str(sensor),
                        self._as_optional_float(value),
                        None,
                        room,
                        source,
                        raw_json,
                    )
                )

        return rows


    @staticmethod
    def _as_optional_float(value: Any) -> float | None:
        if value is None:
            return None

        if isinstance(value, bool):
            return float(int(value))

        try:
            return float(value)
        except (TypeError, ValueError):
            return None


    @staticmethod
    def _as_optional_str(value: Any) -> str | None:
        if value is None:
            return None

        return str(value)

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
        snapshot_path: str | None = None,
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
                        (command_id, person_name, confidence, status, triggered_by,
                        device, room, action_result, snapshot_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        command_id,
                        person_name,
                        confidence,
                        status,
                        triggered_by,
                        device,
                        room,
                        action_result,
                        snapshot_path,
                    ),
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
        terminal_statuses = {
            "success",
            "failed",
            "rejected",
            "clarify",
            "registry_request",
            "skipped",
        }
        should_complete = int(execution_status in terminal_statuses)

        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                conn.execute(
                    """
                    UPDATE command_log
                    SET result = ?,
                        execution_status = COALESCE(?, execution_status),
                        error_message = COALESCE(?, error_message),
                        completed_at = CASE
                            WHEN ? = 1
                            THEN COALESCE(
                                completed_at,
                                strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
                            )
                            ELSE completed_at
                        END,
                        latency_ms = CASE
                            WHEN ? = 1 AND started_at IS NOT NULL
                            THEN CAST(
                                (
                                    julianday(
                                        COALESCE(
                                            completed_at,
                                            strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')
                                        )
                                    ) - julianday(started_at)
                                ) * 86400000 AS INTEGER
                            )
                            ELSE latency_ms
                        END
                    WHERE id = ?
                    """,
                    (
                        result,
                        execution_status,
                        error_message,
                        should_complete,
                        should_complete,
                        command_id,
                    ),
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
