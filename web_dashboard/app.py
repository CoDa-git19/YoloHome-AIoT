"""
Web Dashboard (Flask).

Giao diện web cho gateway YoloHome-AIoT. Toàn bộ pipeline thật
(STT -> LLM -> Validation -> Face Auth -> Execution) chạy qua đây và ghi log
thật vào database/yolohome.db theo database/schema.sql.

VÌ SAO DÙNG LẠI MainOrchestrator THAY VÌ TỰ LẮP RÁP
---------------------------------------------------
Bản trước tự dựng lại CommandService/AuthService/RuleService/observers ngay
trong file này - tức là một bản sao song song của system_core/main.py. Hai bản
lắp ráp cho cùng một hệ thống chắc chắn sẽ trôi lệch, và nó đã xảy ra thật:
AuthService đổi chữ ký thành (face_recognizer, command_service, ...) thì
main.py cập nhật theo, còn dashboard vẫn gọi AuthService(command_service) và
hỏng ngay từ lúc import.

MainOrchestrator là "cổng vào duy nhất" theo thiết kế. Dashboard chỉ nên là
một lớp vỏ HTTP quanh nó, không phải người lắp ráp thứ hai.

Dashboard vẫn gọi handle_transcript() ở mức có cấu trúc (thay vì
process_text_command() trả về chuỗi), vì UI cần command JSON, trạng thái từng
bước pipeline và latency - những thứ một chuỗi trả lời không mang theo được.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from flask import (Flask, jsonify, redirect, render_template, request, send_from_directory, url_for)

from config import settings
from database.init_db import init_db
from modules.llm_integration.llm_module import load_device_registry
from system_core.main import MainOrchestrator

app = Flask(__name__)

DB_PATH: Path = settings.DB_PATH

# --- Database: dùng đúng schema thật (database/schema.sql), idempotent ---
init_db()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# Face Auth trong ngữ cảnh web chạy HEADLESS: không mở cửa sổ OpenCV.
#
# capture_frame() mặc định gọi cv2.imshow(), nhưng ở đây nó sẽ chạy trong
# thread worker của Flask chứ không phải main thread. GUI của OpenCV không an
# toàn khi gọi ngoài main thread (trên Windows có thể treo hẳn request).
# Người dùng đã có phản hồi trạng thái ngay trên dashboard nên cửa sổ đó thừa.
FACE_SHOW_WINDOW = False


# =============================================================================
# Gateway: một MainOrchestrator duy nhất cho cả tiến trình Flask
# =============================================================================

orchestrator = MainOrchestrator()

# Vòng đọc cảm biến = nhịp tim của automation rule. Không chạy thì rule đã lưu
# trong database sẽ không bao giờ kích hoạt.
_sensor_thread = threading.Thread(
    target=orchestrator.start_sensor_loop,
    daemon=True,
)
_sensor_thread.start()

# Pipeline chạy tuần tự: một lệnh có thể mất tới FACE_SCAN_TIMEOUT_SECONDS
# (quét khuôn mặt) hoặc vài giây (STT). Không khoá thì hai request song song sẽ
# tranh nhau camera và micro. Endpoint trạng thái/log KHÔNG giữ khoá này nên
# dashboard vẫn cập nhật bình thường trong lúc chờ.
_pipeline_lock = threading.Lock()


def _install_headless_frame_capturer() -> None:
    """
    Thay frame_capturer của orchestrator bằng bản không mở cửa sổ.

    _build_face() chỉ gán frame_capturer ở chế độ face THẬT. Ở chế độ mock nó
    để None, mà AuthService lại từ chối khi frame=None (fail closed) - nghĩa là
    USE_MOCK_FACE=true sẽ chặn mọi lệnh mở cửa. Mock recognizer bỏ qua hoàn
    toàn tham số frame, nên ở đúng chế độ đó ta đưa một frame giả để đường ống
    chạy được. Đây KHÔNG phải lỗ hổng: mock vốn đã tự nhận mọi khuôn mặt, và
    check_config() cảnh báo mỗi lần khởi động.
    """
    if orchestrator.face_module is None:
        return

    if orchestrator.use_mock_face:
        orchestrator.frame_capturer = lambda: "mock-frame"
        return

    try:
        from modules.face_recognition.face_module import capture_frame
    except (Exception, SystemExit) as exc:
        print(f"[web_dashboard] cannot import capture_frame: {exc}")
        return

    orchestrator.frame_capturer = lambda: capture_frame(
        timeout_seconds=settings.FACE_SCAN_TIMEOUT_SECONDS,
        require_blink=settings.FACE_REQUIRE_BLINK,
        camera_index=settings.CAMERA_INDEX,
        show_window=FACE_SHOW_WINDOW,
    )


_install_headless_frame_capturer()


def device_state(device: str, room: str) -> str | None:
    result = orchestrator.hardware_module.execute_command(
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

@app.route('/snapshots/<path:filename>')
def serve_snapshot(filename: str):
    """
    Phục vụ ảnh chụp lúc xác thực khuôn mặt.

    DÙNG send_from_directory, KHÔNG ghép chuỗi đường dẫn.

    Thư mục này chứa ảnh khuôn mặt người thật, và tên file đến TỪ DATABASE
    rồi đi thẳng vào URL. Ghép chuỗi thủ công sẽ mở đường cho path traversal:
    một hàng face_log có snapshot_path = "../../config/.env" là đủ để lộ
    GEMINI_API_KEY và ADAFRUIT_IO_KEY. send_from_directory chuẩn hoá đường dẫn
    và từ chối mọi thứ nằm ngoài thư mục gốc.

    Ảnh chưa từng được ghi (mất file, đĩa đầy) -> 404, và template hiển thị
    dấu gạch ngang. Không cần xử lý riêng ở đây.
    """
    return send_from_directory(settings.SNAPSHOTS_DIR, filename)

# =============================================================================
# API: Trạng thái thiết bị / cảm biến / kết nối (sidebar + top metrics)
# =============================================================================

def _module_status() -> dict[str, Any]:
    """
    Tình trạng THẬT của từng module, để dashboard nói rõ cái gì đang chạy.

    Không có phần này thì các chế độ hỏng đều trông giống nhau trên UI: thiếu
    face_model.pkl, thiếu ffmpeg, hay đang chạy mock - tất cả chỉ hiện ra dưới
    dạng "lệnh mở cửa bị từ chối" mà không ai biết vì sao.
    """
    stt_ready = orchestrator.stt_engine is not None
    if not stt_ready:
        stt_mode = "unavailable"
    elif orchestrator.use_mock_stt:
        stt_mode = "mock"
    else:
        stt_mode = "real"

    face_ready = orchestrator.face_module is not None
    if not face_ready:
        face_mode = "unavailable"
    elif orchestrator.use_mock_face:
        face_mode = "mock"
    else:
        face_mode = "real"

    return {
        "stt": {
            "ready": stt_ready,
            "mode": stt_mode,
            "engine": settings.STT_ENGINE if stt_mode == "real" else None,
            "model": (
                settings.CT2_MODEL_PATH
                if settings.STT_ENGINE == "faster-whisper"
                else settings.PHOWHISPER_MODEL
            ) if stt_mode == "real" else None,
        },
        "face": {
            "ready": face_ready,
            "mode": face_mode,
            # Thiếu model là nguyên nhân phổ biến nhất khiến Face Auth tắt.
            "model_present": settings.FACE_MODEL_PATH.exists(),
            "require_blink": settings.FACE_REQUIRE_BLINK,
            "threshold": settings.FACE_AUTH_THRESHOLD,
        },
        "llm": {
            "mode": "mock" if orchestrator.use_mock_llm else "gemini",
            "model": None if orchestrator.use_mock_llm else settings.GEMINI_MODEL,
        },
        "hardware": {"mode": orchestrator.hardware_mode},
    }


@app.route('/api/status')
def api_status():
    sensors = dict(orchestrator.latest_sensor_data or {})

    devices = {
        "light": device_state("light", "living_room") or "off",
        "fan": device_state("fan", "living_room") or "off",
        "door": device_state("door", "main_door") or "closed",
    }

    # Ở chế độ mô phỏng KHÔNG có kết nối Adafruit nào cả. Báo "connected" chỉ
    # vì .env có sẵn username/key là nói dối người xem dashboard.
    adafruit_connected = (
        orchestrator.hardware_mode == "real"
        and bool(settings.ADAFRUIT_IO_USERNAME and settings.ADAFRUIT_IO_KEY)
    )

    return jsonify({
        "sensors": sensors,
        "devices": devices,
        "modules": _module_status(),
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
    source: str = "text",
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

    # face_log.status theo schema: authorized | denied | no_face | timeout.
    # Ánh xạ sang câu giải thích được, vì action_result chỉ là "rejected" -
    # không cho biết bị từ chối vì người lạ, vì thiếu model, hay vì hết giờ.
    face_reasons = {
        "authorized": "Đã xác thực thành công.",
        "denied": "Từ chối: không phải người nhà, hoặc độ tin cậy dưới ngưỡng cho phép.",
        "no_face": "Không lấy được khuôn mặt (camera, model, hoặc hết thời gian chờ).",
        "timeout": "Hết thời gian chờ xác thực.",
    }

    if not requires_auth:
        face_step = {"status": "skipped", "detail": "Lệnh này không cần xác thực."}
    elif face_row is not None:
        face_status = str(face_row.get('status') or '')
        authorized = face_status == 'authorized'
        face_step = {
            "status": "passed" if authorized else "failed",
            "person_name": face_row.get('person_name'),
            "confidence": face_row.get('confidence'),
            "detail": face_reasons.get(face_status, "Xác thực không thành công."),
        }
    else:
        face_step = {"status": "pending", "detail": "Đang chờ xác thực khuôn mặt."}

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
        "stt": {
            "status": "done",
            "source": source,
            # PhoWhisper không trả về điểm tin cậy, và ở chế độ nhập text thì
            # khái niệm đó không tồn tại. Trả None để UI hiện "N/A" thay vì bịa
            # ra một con số trông như thật.
            "confidence": None,
        },
        "llm": {"status": "done" if command else "failed"},
        "validation": validation_step,
        "face_auth": face_step,
        "execution": execution_step,
    }

    # AuthService dựng câu trả lời bằng `result["response"] or ...`, mà ở nhánh
    # auth_required thì chuỗi đó đang là "Thiết bị này yêu cầu xác thực khuôn
    # mặt để kích hoạt." Kết quả: mở cửa THÀNH CÔNG vẫn hiện câu đòi xác thực.
    # Dựng lại câu trả lời từ trạng thái đã ghi trong database - nguồn sự thật
    # duy nhất - thay vì tin vào chuỗi được truyền qua nhiều lớp.
    response_text = result.get('response')

    if (
        requires_auth
        and face_step.get("status") == "passed"
        and execution_state == "success"
    ):
        person = face_step.get("person_name") or "người dùng"
        response_text = f"Đã xác thực {person}. Đã thực hiện lệnh thành công."

    return {
        "command_id": result.get('command_id'),
        "transcript": transcript,
        "source": source,
        "command": command,
        "response": response_text,
        "pipeline": pipeline,
        "latency_ms": (command_row or {}).get('latency_ms') or result.get('total_latency_ms'),
        "session_id": result.get('session_id'),
        "awaiting_reply": result.get('awaiting_reply'),
    }


def _run_pipeline(
    transcript: str,
    session_id: str | None,
    source: str,
) -> dict[str, Any]:
    """
    Chạy một transcript qua pipeline thật rồi dựng payload cho dashboard.

    Dùng chung cho cả đường văn bản (/api/command) và giọng nói (/api/voice) -
    sau bước STT thì hai đường hoàn toàn giống nhau, đúng như main.py.
    """
    with _pipeline_lock:
        result = orchestrator.command_service.handle_transcript(
            transcript,
            sensor_data=orchestrator.latest_sensor_data,
            session_id=session_id,
        )

        if result.get('next_step') == 'auth_required':
            # Ủy quyền cho orchestrator: nó sở hữu camera (Contract B) và xử lý
            # cả hai nhánh - AuthService thật, hoặc fail-closed khi face module
            # chưa sẵn sàng - đồng thời ĐÓNG command_log/face_log. Dựng lại
            # đoạn này ở đây sẽ tái tạo đúng kiểu trôi lệch mà file này vừa mắc.
            response = orchestrator.handle_auth_required(result)
            result['response'] = response or result.get('response')

    command_id = int(result.get('command_id') or -1)
    command_row = _fetch_command_row(command_id)
    face_row = _fetch_latest_face_row(command_id)

    return _build_pipeline_payload(
        transcript, result, command_row, face_row, source=source
    )


@app.route('/api/command', methods=['POST'])
def api_command():
    payload = request.get_json(silent=True) or {}
    transcript = str(payload.get('transcript') or '').strip()
    session_id = payload.get('session_id') or None

    if not transcript:
        return jsonify({"error": "transcript is required"}), 400

    return jsonify(_run_pipeline(transcript, session_id, source="text"))


@app.route('/api/voice', methods=['POST'])
def api_voice():
    """
    Contract A: audio -> STT -> pipeline chung.

    Nhận audio ở ĐỊNH DẠNG BẤT KỲ (trình duyệt thường gửi webm/opus qua
    MediaRecorder); stt_module tự chuẩn hoá bằng ffmpeg trước khi đưa vào model.
    """
    if orchestrator.stt_engine is None:
        return jsonify({
            "error": "stt_unavailable",
            "message": "Chưa cấu hình nhận dạng giọng nói. Bạn hãy nhập bằng văn bản.",
        }), 503

    if 'audio' in request.files:
        audio_bytes = request.files['audio'].read()
    else:
        audio_bytes = request.get_data()

    if not audio_bytes:
        return jsonify({
            "error": "empty_audio",
            "message": "Không nhận được dữ liệu âm thanh.",
        }), 400

    session_id = request.form.get('session_id') or None

    with _pipeline_lock:
        try:
            transcript = orchestrator.stt_engine.transcribe(audio_bytes)
        except Exception as exc:
            return jsonify({
                "error": "stt_failed",
                "message": f"Không nhận dạng được giọng nói: {exc}",
            }), 500

    # transcribe() nuốt mọi lỗi và trả "" - thiếu ffmpeg, audio hỏng, hoặc
    # người dùng không nói gì đều rơi vào đây.
    if not transcript or not transcript.strip():
        return jsonify({
            "error": "empty_transcript",
            "message": (
                "Mình chưa nghe rõ. Bạn nói lại giúp mình nhé "
                "(nếu lỗi lặp lại, kiểm tra xem máy chủ đã cài ffmpeg chưa)."
            ),
        }), 422

    return jsonify(_run_pipeline(transcript.strip(), session_id, source="voice"))


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

                -- execution_status có nhiều hơn 2 trạng thái, và gộp tất cả
                -- những gì != 'success' vào "thất bại" sẽ dìm KPI xuống thấp
                -- hơn thực tế: 'clarify' là đang hỏi lại người dùng,
                -- 'scheduled' là đã hẹn giờ thành công, 'registry_request' là
                -- thiết bị chưa đăng ký (lỗi cấu hình, không phải lỗi pipeline).
                --
                -- 'failed' cố ý định nghĩa bằng PHỦ ĐỊNH hai nhóm kia, để nếu
                -- sau này có thêm trạng thái mới thì nó rơi vào mẫu số chứ
                -- không biến mất im lặng. Bất biến: success + failed + pending
                -- LUÔN bằng total.
                SUM(CASE WHEN execution_status IN ('success','scheduled')
                         THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN execution_status NOT IN
                              ('success','scheduled','clarify','registry_request','waiting_auth')
                         THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN execution_status IN ('clarify','registry_request','waiting_auth')
                         THEN 1 ELSE 0 END) AS pending,

                -- Tách riêng khỏi 'failed' (là tập con) vì dashboard có ô KPI
                -- riêng cho số lệnh bị Face Auth từ chối.
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
    failed = row['failed'] or 0
    pending = row['pending'] or 0
    rejected = row['rejected'] or 0
    requires_auth = row['requires_auth'] or 0

    # Mẫu số của tỉ lệ thành công là các lệnh ĐÃ NGÃ NGŨ, không phải tất cả.
    # Một lệnh đang chờ người dùng trả lời chưa thành công cũng chưa thất bại;
    # đưa nó vào mẫu số nghĩa là mỗi lần hệ thống hỏi lại - tức làm ĐÚNG - lại
    # tự trừ điểm chính mình.
    decided = success + failed

    def pct(count: int, base: int) -> float:
        return round(count / base * 100, 1) if base else 0.0

    return jsonify({
        "total": total,
        "success": success,
        "success_rate": pct(success, decided),
        "failed": failed,
        "pending": pending,
        "rejected": rejected,
        "rejected_rate": pct(rejected, decided),
        "requires_auth": requires_auth,
        # Giữ mẫu số là total: đây là "bao nhiêu phần lệnh cần xác thực",
        # một câu hỏi về TOÀN BỘ lưu lượng, không phải về kết quả.
        "requires_auth_rate": pct(requires_auth, total),
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

