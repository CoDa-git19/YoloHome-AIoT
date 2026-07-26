# Database Module

YoloHome uses a single **SQLite** database (`database/yolohome.db`) for persistent
state: command audit logs, face-authentication records, automation rules, scheduled
commands, and error diagnostics. No external database server is required.

---

## File layout

```text
database/
├── init_db.py      # Python initializer — reads schema.sql and creates tables
├── schema.sql      # Source of truth for database DDL
├── yolohome.db     # Runtime SQLite file — created automatically, excluded from git
└── README_DB.md    # Database documentation
```

The database path is resolved in `config/settings.py`:

```python
DB_PATH = ROOT_DIR / "database" / "yolohome.db"
SCHEMA_PATH = ROOT_DIR / "database" / "schema.sql"
```

---

## Initialization

Call `init_db()` once at application startup. The function reads `schema.sql`, creates
the `database/` directory if needed, and executes all `CREATE TABLE IF NOT EXISTS`
statements.

```python
from database.init_db import init_db
init_db()
```

Because the schema uses `CREATE TABLE IF NOT EXISTS`, repeated calls are safe.

---

## Schema

### `command_log`

Records every voice/text command processed by the pipeline.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default local now | Creation time |
| `transcript` | TEXT | NOT NULL | Raw input text from STT/user |
| `json_cmd` | TEXT | NOT NULL | JSON-serialized parsed command |
| `intent` | TEXT | | `control_device`, `query_status`, `create_rule`, `clarify`, `reject` |
| `action` | TEXT | | `turn_on`, `turn_off`, `open`, `close`, `get_status` |
| `device` | TEXT | | `light`, `fan`, `door` |
| `room` | TEXT | | `living_room`, `bedroom`, `main_door` |
| `face_auth` | INTEGER | NOT NULL, default 0 | 1 if command requires face authentication |
| `validation_status` | TEXT | | `passed` / `failed` |
| `execution_status` | TEXT | | `pending`, `success`, `waiting_auth`, `clarify`, `rejected`, `failed`, `skipped` |
| `result` | TEXT | NOT NULL | Final or intermediate pipeline result |
| `started_at` | TEXT | NOT NULL, default local now with milliseconds | Pipeline start time |
| `completed_at` | TEXT | nullable | Pipeline completion time; NULL while pending/waiting_auth |
| `latency_ms` | INTEGER | | End-to-end pipeline latency in milliseconds, finalized when command completes |
| `error_message` | TEXT | | Populated only on failure when possible |

Recommended `result` values:

```text
success
waiting_auth
fail: <reason>
rejected: face_auth
rejected: unknown_device
rejected: validation
error: llm_parse
error: stt_timeout
```

Example command lifecycle:

```text
"bật đèn phòng khách"
→ validation_status = passed
→ execution_status = success
→ result = success

"mở cửa chính"
→ validation_status = passed
→ execution_status = waiting_auth
→ result = waiting_auth

"bật máy lạnh phòng bếp"
→ validation_status = passed
→ execution_status = rejected
→ result = rejected: unknown_device
```

---

### `face_log`

Records every face-authentication event, optionally linked to a command.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default local now | Creation time |
| `command_id` | INTEGER | FK → `command_log.id`, nullable | Related command, nullable for manual tests |
| `person_name` | TEXT | NOT NULL | Recognized person or `Unknown` |
| `confidence` | REAL | NOT NULL | Recognition score in `[0.0, 1.0]` |
| `status` | TEXT | NOT NULL | `authorized`, `denied`, `no_face`, `timeout` |
| `triggered_by` | TEXT | | Reason for auth, e.g. `sensitive_command`, `manual_test`, `dashboard_unlock` |
| `device` | TEXT | | Snapshot of target device |
| `room` | TEXT | | Snapshot of target room |
| `action_result` | TEXT | | `executed`, `cancelled`, `failed` |
| `snapshot_path` | TEXT | | Optional path to captured face snapshot; NULL if not stored |

Relationship:

```text
command_log 1 ─── 0..* face_log
```

A command can have zero or multiple face-auth attempts.

---

### `sensor_log`

Records sensor readings received from hardware, MQTT, Adafruit IO, or mock sensor sources.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default local now with milliseconds | Reading time |
| `sensor` | TEXT | NOT NULL | Sensor name, e.g. `temperature`, `humidity`, `light`, `motion` |
| `value` | REAL | | Numeric reading when available |
| `unit` | TEXT | | Optional unit, e.g. `C`, `%`, `lux` |
| `room` | TEXT | | Optional room/location |
| `source` | TEXT | | Source module, e.g. `hardware`, `mqtt`, `adafruit`, `mock` |
| `raw_json` | TEXT | | Original JSON payload for debugging |

---

### `automation_rules`

Stores automation rules created from `create_rule` commands.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Rule identifier |
| `command_id` | INTEGER | FK → `command_log.id`, nullable | Command that created the rule |
| `sensor` | TEXT | NOT NULL | Sensor name, e.g. `temperature`, `humidity`, `light`, `motion` |
| `operator` | TEXT | NOT NULL | `>`, `<`, `>=`, `<=`, `==` |
| `value` | REAL | NOT NULL | Threshold value |
| `action` | TEXT | NOT NULL | Device action to execute |
| `device` | TEXT | NOT NULL | Target device |
| `room` | TEXT | NOT NULL | Target room |
| `is_active` | INTEGER | NOT NULL, default 1 | 1 if rule is active |
| `created_at` | TEXT | NOT NULL, default local now | Creation time |

Example:

```text
Nếu nhiệt độ trên 30 độ thì bật quạt phòng khách
→ sensor = temperature
→ operator = >
→ value = 30
→ action = turn_on
→ device = fan
→ room = living_room
```

---

### `schedule`

Stores one-shot scheduled commands to be executed at a future datetime.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `run_at` | TEXT | NOT NULL | Target datetime as `YYYY-MM-DD HH:MM:SS` |
| `json_cmd` | TEXT | NOT NULL | JSON-serialized command payload |
| `is_active` | INTEGER | NOT NULL, default 1 | Set to 0 once fired |

---

### `error_log`

Lightweight diagnostic log written by modules on non-fatal failures.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default local now | Creation time |
| `module` | TEXT | NOT NULL | Source module tag |
| `message` | TEXT | NOT NULL | Human-readable error description |

Recommended module tags:

```text
stt
llm
face
hw
adafruit
db
scheduler
gateway
command_service
rule_service
dashboard
```

---

## Entity-relationship overview

```text
command_log ──< face_log
command_log ──< automation_rules
sensor_log     standalone time-series readings
schedule       standalone
error_log      standalone
```

---

## Python API

### `database.init_db`

```python
from database.init_db import init_db

init_db()
```

### `services.logging_service`

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

Method signatures:

```python
log_error(module: str, message: str) -> None

log_command(
    transcript: str,
    json_cmd: dict,
    result: str,
    validation_status: str | None = None,
    execution_status: str | None = None,
    latency_ms: int | None = None,
    error_message: str | None = None,
) -> int

log_face(
    person_name: str,
    confidence: float,
    status: str,
    command_id: int | None = None,
    triggered_by: str | None = None,
    device: str | None = None,
    room: str | None = None,
    action_result: str | None = None,
    snapshot_path: str | None = None,
) -> None

update_command_result(
    command_id: int,
    result: str,
    execution_status: str | None = None,
    error_message: str | None = None,
) -> None

add_schedule(run_at: str, json_cmd: dict) -> int

get_schedules() -> list[dict]
```

### `services.rule_service`

```python
from services.rule_service import RuleService

rule_service = RuleService()
rule_id = rule_service.create_rule(command_id, command)
active_rules = rule_service.get_active_rules()
actions = rule_service.evaluate_sensor_data({"temperature": 35})
```

---

## Notes

- LLM does not execute hardware directly.
- LLM returns structured command data and `next_step`.
- `waiting_auth` is generated by `CommandService`, not by the LLM.
- `command_log.result` is the pipeline/result convention used by Database, Dashboard, and reporting.
- `schema.sql` should be treated as the source of truth for table definitions.
