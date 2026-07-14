CREATE TABLE IF NOT EXISTS command_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    transcript         TEXT    NOT NULL,
    json_cmd           TEXT    NOT NULL,
    intent             TEXT,
    action             TEXT,
    device             TEXT,
    room               TEXT,
    face_auth          INTEGER NOT NULL DEFAULT 0,
    validation_status  TEXT,
    execution_status   TEXT,
    result             TEXT    NOT NULL,
    started_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
    completed_at       TEXT,
    latency_ms         INTEGER,
    error_message      TEXT
);

CREATE TABLE IF NOT EXISTS face_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    command_id    INTEGER,
    person_name   TEXT    NOT NULL,
    confidence    REAL    NOT NULL,
    status        TEXT    NOT NULL,
    triggered_by  TEXT,
    device        TEXT,
    room          TEXT,
    action_result TEXT,
    snapshot_path TEXT,
    FOREIGN KEY (command_id) REFERENCES command_log(id)
);

CREATE TABLE IF NOT EXISTS sensor_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
    sensor    TEXT    NOT NULL,
    value     REAL,
    unit      TEXT,
    room      TEXT,
    source    TEXT,
    raw_json  TEXT
);

CREATE TABLE IF NOT EXISTS schedule (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at    TEXT    NOT NULL,
    json_cmd  TEXT    NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS error_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    module    TEXT    NOT NULL,
    message   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_rules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id        INTEGER,
    sensor            TEXT    NOT NULL,
    operator          TEXT    NOT NULL,
    value             REAL    NOT NULL,
    action            TEXT    NOT NULL,
    device            TEXT    NOT NULL,
    room              TEXT    NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1,
    -- last_state: điều kiện có đang đúng ở lần đọc sensor TRƯỚC hay không.
    -- Rule chỉ kích hoạt khi chuyển false -> true (edge-triggered),
    -- nếu không sẽ spam lệnh xuống phần cứng mỗi vòng đọc sensor.
    last_state        INTEGER NOT NULL DEFAULT 0,
    last_triggered_at TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (command_id) REFERENCES command_log(id)
);

-- Chặn rule trùng lặp: nói 2 lần cùng một câu không tạo ra 2 rule giống hệt.
CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_rules_unique
ON automation_rules(sensor, operator, value, action, device, room)
WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS idx_command_log_timestamp
ON command_log(timestamp);

CREATE INDEX IF NOT EXISTS idx_command_log_execution_status
ON command_log(execution_status);

CREATE INDEX IF NOT EXISTS idx_face_log_command_id
ON face_log(command_id);

CREATE INDEX IF NOT EXISTS idx_sensor_log_sensor_timestamp
ON sensor_log(sensor, timestamp);