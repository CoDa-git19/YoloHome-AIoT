# Database Module

YoloHome uses a single **SQLite** database (`database/yolohome.db`) for all persistent
state: command audit logs, face-authentication records, scheduled commands, and error
diagnostics. No external database server is required.

---

## File layout

```
database/
├── init_db.py      # Python initialiser — call init_db() to create/migrate tables
├── schema.sql      # Raw DDL — same CREATE TABLE statements as init_db.py
├── yolohome.db     # Runtime file — created automatically, excluded from git
└── README_DB.md    # This file
```

The database path is resolved in `config/settings.py`:

```python
DB_PATH = ROOT_DIR / "database" / "yolohome.db"
```

It can be overridden via the `DATABASE_URL` environment variable
(format: `sqlite:///relative/path/to/file.db`).

---

## Initialisation

Call `init_db()` once at application startup (all statements use `CREATE TABLE IF NOT EXISTS`,
so repeated calls are safe):

```python
from database.init_db import init_db
init_db()
```

The function also creates the `database/` directory if it does not already exist.

---

## Schema

### `command_log`

Records every voice / text command processed by the pipeline.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default now | `datetime('now', 'localtime')` |
| `transcript` | TEXT | NOT NULL | Raw text from STT module |
| `json_cmd` | TEXT | NOT NULL | JSON-serialised parsed command |
| `intent` | TEXT | | e.g. `"control_device"` |
| `action` | TEXT | | e.g. `"turn_on"`, `"turn_off"` |
| `device` | TEXT | | e.g. `"light"`, `"fan"`, `"lock"` |
| `room` | TEXT | | e.g. `"living_room"`, `"bedroom"` |
| `face_auth` | INTEGER | NOT NULL, default 0 | 1 if command required face auth |
| `validation_status` | TEXT | | `"valid"` / `"invalid"` |
| `execution_status` | TEXT | | `"ack"` / `"skipped"` / `"error"` |
| `result` | TEXT | NOT NULL | `"success"` / `"waiting_auth"` / `"error"` |
| `latency_ms` | INTEGER | | End-to-end pipeline latency |
| `error_message` | TEXT | | Populated on failure |

---

### `face_log`

Records every face-recognition event, optionally linked to a command.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default now | `datetime('now', 'localtime')` |
| `command_id` | INTEGER | FK → `command_log.id` | Linked command (nullable) |
| `person_name` | TEXT | NOT NULL | Recognised person or `"Unknown"` |
| `confidence` | REAL | NOT NULL | Recognition score in `[0.0, 1.0]` |
| `status` | TEXT | NOT NULL | `"authorized"` / `"denied"` / `"no_face"` / `"timeout"` |
| `triggered_by` | TEXT | | Original transcript that triggered auth |
| `device` | TEXT | | Target device |
| `room` | TEXT | | Target room |
| `action_result` | TEXT | | `"executed"` / `"cancelled"` |

---

### `schedule`

Stores one-shot scheduled commands to be executed at a future datetime.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `run_at` | TEXT | NOT NULL | Target datetime as `"YYYY-MM-DD HH:MM:SS"` |
| `json_cmd` | TEXT | NOT NULL | JSON-serialised command payload |
| `is_active` | INTEGER | NOT NULL, default 1 | Set to 0 once fired |

`get_schedules()` in `LoggingService` fetches all active rows whose `run_at` is ≤ now
and atomically marks them as inactive in the same transaction.

---

### `error_log`

Lightweight diagnostic log written by every module on non-fatal failures.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default now | `datetime('now', 'localtime')` |
| `module` | TEXT | NOT NULL | Source module tag (e.g. `"hw"`, `"llm"`, `"face"`) |
| `message` | TEXT | NOT NULL | Human-readable error description |

Valid module tags: `stt`, `llm`, `face`, `hw`, `adafruit`, `db`, `scheduler`, `gateway`.

---

## Entity-relationship overview

```
command_log ──< face_log      (face_log.command_id → command_log.id, nullable)
command_log     (standalone)
schedule        (standalone)
error_log       (standalone)
```

---

## Python API — `LoggingService`

All database writes go through `services/logging_service.py`.
Import the singleton aliases for drop-in use:

```python
from services.logging_service import (
    log_error,
    log_command,
    log_face,
    update_command_result,
    add_schedule,
    get_schedules,
)
```

Or use the class directly for Observer-pattern wiring:

```python
from services.logging_service import LoggingService
from modules.hardware_gateway.hardware_module import HardwareModule

hw = HardwareModule()
hw.attach(LoggingService())   # sensor events will be logged automatically
```

### Method signatures

```python
log_error(module: str, message: str) -> None
log_command(transcript, json_cmd, result, validation_status=None,
            execution_status=None, latency_ms=None, error_message=None) -> int
log_face(person_name, confidence, status, command_id=None,
         triggered_by=None, device=None, room=None, action_result=None) -> None
update_command_result(command_id, result, execution_status=None,
                      error_message=None) -> None
add_schedule(run_at: str, json_cmd: dict) -> int
get_schedules() -> list[dict]
```
