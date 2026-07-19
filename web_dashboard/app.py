"""
Web Dashboard (Flask).

Đây là một "gateway" nhẹ nhúng thẳng CommandService/AuthService/RuleService
thật (cùng những class main.py dùng), để nút Send trên Agent Console chạy
qua đúng pipeline STT->LLM->Validation->FaceAuth->Execution và ghi log thật
vào database/yolohome.db (theo database/schema.sql).

Không có STT/camera thật trong ngữ cảnh web, nên:
- "STT" chỉ là text nhập tay (fallback).
- Face Auth dùng MockFaceRecognizer (không có khung hình camera thật).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for

from config import settings
from database.init_db import init_db
from modules.llm_integration.llm_module import load_device_registry
from services.auth_service import AuthService
from services.command_service import CommandService, MockHardwareModule
from services.rule_service import RuleService
from system_core.observers import RuleObserver

app = Flask(__name__)

DB_PATH: Path = settings.DB_PATH

# --- Database: dùng đúng schema thật (database/schema.sql), idempotent ---
init_db()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# Wire pipeline thật (mirrors system_core/main.py, nhúng trong tiến trình Flask)
# =============================================================================

rule_service = RuleService()
# Giữ một reference kiểu cụ thể (MockHardwareModule), thay vì đọc lại qua
# command_service.hardware_module - vốn được khai kiểu HardwareReceiver
# (Protocol chỉ có execute_command), nên sẽ thiếu attach/read_sensors/poll_sensors
# dưới con mắt type checker dù runtime luôn là MockHardwareModule.
hardware_module = MockHardwareModule()
command_service = CommandService(
    rule_service=rule_service,
    hardware_module=hardware_module,
    use_mock=settings.USE_MOCK_LLM,
)
auth_service = AuthService(command_service)
# CommandExecutor Protocol names its parameter "command",
# CommandService.execute_authorized_command names it "command_data"; every
# call site in the codebase passes it positionally, so this is a harmless
# structural-typing mismatch, not a runtime bug.
hardware_module.attach(RuleObserver(rule_service, command_service))  # type: ignore[arg-type]

_latest_sensor_data: dict[str, Any] = hardware_module.read_sensors()
_sensor_lock = threading.Lock()


def _sensor_loop() -> None:
    global _latest_sensor_data
    while True:
        try:
            data = hardware_module.poll_sensors()
            with _sensor_lock:
                _latest_sensor_data = data
        except Exception as exc:  # pragma: no cover - background loop safety net
            print(f"[web_dashboard] sensor loop error: {exc}")
        time.sleep(3)


_sensor_thread = threading.Thread(target=_sensor_loop, daemon=True)
_sensor_thread.start()


def device_state(device: str, room: str) -> str | None:
    result = hardware_module.execute_command(
        {"device": device, "room": room, "action": "get_status"}
    )
    return result.get("state")


# =============================================================================
# ROUTES CHO GIAO DIỆN (FRONTEND VIEWS)
# =============================================================================

@app.route('/')
def index():
    return redirect(url_for('agent_console'))


@app.route('/console')
def agent_console():
    return render_template('agent_console.html', active_page='console')


@app.route('/command-log')
def command_log_page():
    return render_template('command_log.html', active_page='command_log')


@app.route('/face-auth-log')
def face_auth_log_page():
    return render_template('face_auth_log.html', active_page='face_auth')


# =============================================================================
# API: Trạng thái thiết bị / cảm biến / kết nối (sidebar + top metrics)
# =============================================================================

@app.route('/api/status')
def api_status():
    with _sensor_lock:
        sensors = dict(_latest_sensor_data)

    devices = {
        "light": device_state("light", "living_room") or "off",
        "fan": device_state("fan", "living_room") or "off",
        "door": device_state("door", "main_door") or "closed",
    }

    adafruit_connected = bool(settings.ADAFRUIT_IO_USERNAME and settings.ADAFRUIT_IO_KEY)

    return jsonify({
        "sensors": sensors,
        "devices": devices,
        "connections": {
            "gateway": _sensor_thread.is_alive(),
            "adafruit": adafruit_connected,
        },
    })


# =============================================================================
# API: Agent Console - gửi lệnh qua pipeline thật
# =============================================================================

def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _fetch_command_row(command_id: int) -> dict[str, Any] | None:
    if command_id <= 0:
        return None
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM command_log WHERE id = ?", (command_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row)


def _fetch_latest_face_row(command_id: int) -> dict[str, Any] | None:
    if command_id <= 0:
        return None
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM face_log WHERE command_id = ? ORDER BY id DESC LIMIT 1",
            (command_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row)


def _build_pipeline_payload(
    transcript: str,
    result: dict[str, Any],
    command_row: dict[str, Any] | None,
    face_row: dict[str, Any] | None,
) -> dict[str, Any]:
    command = result.get('command') or {}
    requires_auth = bool(command.get('face_auth'))

    validation_status = (command_row or {}).get('validation_status')
    if validation_status is None:
        validation_status = 'passed' if result.get('ok') else 'failed'

    final_result = (command_row or {}).get('result') or result.get('result')
    execution_status = (command_row or {}).get('execution_status') or result.get('execution_status')

    validation_step = {
        "status": "passed" if validation_status == "passed" else "failed",
        "detail": (
            "Command schema and device are valid."
            if validation_status == "passed"
            else (result.get('error') or "Command validation failed.")
        ),
    }

    if not requires_auth:
        face_step = {"status": "skipped", "detail": "Not required for this command."}
    elif face_row is not None:
        authorized = face_row.get('status') == 'authorized'
        face_step = {
            "status": "passed" if authorized else "failed",
            "person_name": face_row.get('person_name'),
            "confidence": face_row.get('confidence'),
            "detail": face_row.get('action_result') or (
                "Access granted." if authorized else "Access denied."
            ),
        }
    else:
        face_step = {"status": "pending", "detail": "Waiting for face authentication."}

    if execution_status in ("pending", "waiting_auth", "clarify", "registry_request"):
        execution_state = "pending"
    elif final_result == "success":
        execution_state = "success"
    elif execution_status == "rejected" or (final_result or "").startswith("rejected"):
        execution_state = "rejected"
    else:
        execution_state = "failed"

    if execution_state == "pending":
        pending_details: dict[str, str] = {
            "clarify": "Waiting for clarification from user.",
            "waiting_auth": "Waiting for face authentication.",
            "registry_request": "Waiting for admin review.",
        }
        detail = pending_details.get(str(execution_status or ""), "Processing.")
    else:
        detail = final_result or execution_status or "pending"

    execution_step = {
        "status": execution_state,
        "detail": detail,
    }

    pipeline = {
        "stt": {"status": "done"},
        "llm": {"status": "done" if command else "failed"},
        "validation": validation_step,
        "face_auth": face_step,
        "execution": execution_step,
    }

    return {
        "command_id": result.get('command_id'),
        "transcript": transcript,
        "command": command,
        "response": result.get('response'),
        "pipeline": pipeline,
        "latency_ms": (command_row or {}).get('latency_ms') or result.get('total_latency_ms'),
        "session_id": result.get('session_id'),
        "awaiting_reply": result.get('awaiting_reply'),
    }


@app.route('/api/command', methods=['POST'])
def api_command():
    payload = request.get_json(silent=True) or {}
    transcript = str(payload.get('transcript') or '').strip()
    session_id = payload.get('session_id') or None

    if not transcript:
        return jsonify({"error": "transcript is required"}), 400

    with _sensor_lock:
        sensor_snapshot = dict(_latest_sensor_data)

    result = command_service.handle_transcript(
        transcript=transcript,
        sensor_data=sensor_snapshot,
        session_id=session_id,
    )

    if result.get('next_step') == 'auth_required':
        auth_outcome = auth_service.authorize_and_execute(result, frame=None)
        result['response'] = auth_outcome.get('response') or result.get('response')

    command_id = int(result.get('command_id') or -1)
    command_row = _fetch_command_row(command_id)
    face_row = _fetch_latest_face_row(command_id)

    return jsonify(_build_pipeline_payload(transcript, result, command_row, face_row))


# =============================================================================
# API: Command Log (KPI + bảng dữ liệu có lọc/phân trang)
# =============================================================================

def _parse_pagination(args) -> tuple[int, int]:
    try:
        page = max(1, int(args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(args.get('page_size', 10))
    except (TypeError, ValueError):
        page_size = 10
    page_size = min(max(page_size, 1), 100)
    return page, page_size


def _build_command_filters(args) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    date_from = (args.get('date_from') or '').strip()
    date_to = (args.get('date_to') or '').strip()
    if date_from:
        clauses.append("timestamp >= ?")
        params.append(f"{date_from} 00:00:00")
    if date_to:
        clauses.append("timestamp <= ?")
        params.append(f"{date_to} 23:59:59")

    device = (args.get('device') or '').strip()
    if device and device.lower() != 'all':
        clauses.append("device = ?")
        params.append(device)

    status = (args.get('status') or '').strip()
    if status and status.lower() != 'all':
        clauses.append("execution_status = ?")
        params.append(status)

    search = (args.get('search') or '').strip()
    if search:
        like = f"%{search}%"
        clauses.append("(transcript LIKE ? OR intent LIKE ? OR device LIKE ?)")
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _command_device_options() -> list[str]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT device FROM command_log WHERE device IS NOT NULL ORDER BY device"
        ).fetchall()
    finally:
        conn.close()

    options = {row['device'] for row in rows if row['device']}
    try:
        registry = load_device_registry()
        for room_devices in registry.values():
            options.update(room_devices.keys())
    except Exception:
        pass

    return sorted(options)


@app.route('/api/command-log/summary')
def api_command_log_summary():
    where, params = _build_command_filters(request.args)

    conn = get_db_connection()
    try:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN execution_status = 'success' THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN execution_status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                SUM(CASE WHEN face_auth = 1 THEN 1 ELSE 0 END) AS requires_auth,
                AVG(latency_ms) AS avg_latency
            FROM command_log
            {where}
            """,
            params,
        ).fetchone()
    finally:
        conn.close()

    total = row['total'] or 0
    success = row['success'] or 0
    rejected = row['rejected'] or 0
    requires_auth = row['requires_auth'] or 0

    def pct(count: int) -> float:
        return round(count / total * 100, 1) if total else 0.0

    return jsonify({
        "total": total,
        "success": success,
        "success_rate": pct(success),
        "rejected": rejected,
        "rejected_rate": pct(rejected),
        "requires_auth": requires_auth,
        "requires_auth_rate": pct(requires_auth),
        "avg_latency_ms": round(row['avg_latency'], 0) if row['avg_latency'] is not None else None,
        "device_options": _command_device_options(),
    })


@app.route('/api/command-log')
def api_command_log_list():
    where, params = _build_command_filters(request.args)
    page, page_size = _parse_pagination(request.args)
    offset = (page - 1) * page_size

    conn = get_db_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM command_log {where}", params
        ).fetchone()['c']

        rows = conn.execute(
            f"""
            SELECT * FROM command_log
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
    finally:
        conn.close()

    total_pages = max(1, (total + page_size - 1) // page_size)

    return jsonify({
        "rows": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    })


# =============================================================================
# API: Face Auth Log (KPI + bảng dữ liệu có lọc/phân trang)
# =============================================================================

# UI gộp 4 trạng thái DB (authorized/denied/no_face/timeout) thành 3 nhóm hiển
# thị (AUTHORIZED/REJECTED/UNKNOWN) cho gọn, giống bản thiết kế tham khảo.
_FACE_STATUS_GROUPS = {
    "authorized": ["authorized"],
    "rejected": ["denied"],
    "unknown": ["no_face", "timeout"],
}


def _build_face_filters(args) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    date_from = (args.get('date_from') or '').strip()
    date_to = (args.get('date_to') or '').strip()
    if date_from:
        clauses.append("face_log.timestamp >= ?")
        params.append(f"{date_from} 00:00:00")
    if date_to:
        clauses.append("face_log.timestamp <= ?")
        params.append(f"{date_to} 23:59:59")

    status = (args.get('status') or '').strip().lower()
    if status and status != 'all' and status in _FACE_STATUS_GROUPS:
        db_statuses = _FACE_STATUS_GROUPS[status]
        placeholders = ','.join('?' for _ in db_statuses)
        clauses.append(f"face_log.status IN ({placeholders})")
        params.extend(db_statuses)

    triggered_by = (args.get('triggered_by') or '').strip()
    if triggered_by and triggered_by.lower() != 'all':
        clauses.append("COALESCE(command_log.transcript, face_log.triggered_by) = ?")
        params.append(triggered_by)

    search = (args.get('search') or '').strip()
    if search:
        like = f"%{search}%"
        clauses.append(
            "(face_log.person_name LIKE ? OR command_log.transcript LIKE ? OR face_log.device LIKE ?)"
        )
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


_FACE_JOIN = """
    FROM face_log
    LEFT JOIN command_log ON face_log.command_id = command_log.id
"""


@app.route('/api/face-log/summary')
def api_face_log_summary():
    where, params = _build_face_filters(request.args)

    conn = get_db_connection()
    try:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN face_log.status = 'authorized' THEN 1 ELSE 0 END) AS authorized,
                SUM(CASE WHEN face_log.status = 'denied' THEN 1 ELSE 0 END) AS rejected,
                SUM(CASE WHEN face_log.status IN ('no_face', 'timeout') THEN 1 ELSE 0 END) AS unknown,
                AVG(face_log.confidence) AS avg_confidence
            {_FACE_JOIN}
            {where}
            """,
            params,
        ).fetchone()

        triggered_rows = conn.execute(
            f"""
            SELECT DISTINCT COALESCE(command_log.transcript, face_log.triggered_by) AS label
            {_FACE_JOIN}
            WHERE COALESCE(command_log.transcript, face_log.triggered_by) IS NOT NULL
            ORDER BY label
            LIMIT 50
            """
        ).fetchall()
    finally:
        conn.close()

    total = row['total'] or 0
    authorized = row['authorized'] or 0
    rejected = row['rejected'] or 0
    unknown = row['unknown'] or 0

    def pct(count: int) -> float:
        return round(count / total * 100, 1) if total else 0.0

    return jsonify({
        "total": total,
        "authorized": authorized,
        "authorized_rate": pct(authorized),
        "rejected": rejected,
        "rejected_rate": pct(rejected),
        "unknown": unknown,
        "unknown_rate": pct(unknown),
        "avg_confidence": round(row['avg_confidence'] * 100, 1) if row['avg_confidence'] is not None else None,
        "triggered_by_options": [r['label'] for r in triggered_rows if r['label']],
    })


@app.route('/api/face-log')
def api_face_log_list():
    where, params = _build_face_filters(request.args)
    page, page_size = _parse_pagination(request.args)
    offset = (page - 1) * page_size

    conn = get_db_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c {_FACE_JOIN} {where}", params
        ).fetchone()['c']

        rows = conn.execute(
            f"""
            SELECT
                face_log.*,
                command_log.transcript AS command_transcript
            {_FACE_JOIN}
            {where}
            ORDER BY face_log.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
    finally:
        conn.close()

    total_pages = max(1, (total + page_size - 1) // page_size)

    return jsonify({
        "rows": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    })


if __name__ == '__main__':
    import os

    port = int(os.getenv("PORT", 5000))
    print(f"[Web Dashboard] Khởi động server tại http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
