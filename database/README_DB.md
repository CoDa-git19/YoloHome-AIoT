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

Both entry points call it: `system_core/main.py` and `web_dashboard/app.py`. Either
can be started first.

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
| `intent` | TEXT | | `control_device`, `query_status`, `create_rule`, `schedule`, `clarify`, `reject` |
| `action` | TEXT | | `turn_on`, `turn_off`, `open`, `close`, `get_status` |
| `device` | TEXT | | `light`, `fan`, `door` |
| `room` | TEXT | | `living_room`, `bedroom`, `main_door` |
| `face_auth` | INTEGER | NOT NULL, default 0 | 1 if command requires face authentication |
| `validation_status` | TEXT | | `passed` / `failed` |
| `execution_status` | TEXT | | See the table below — seven values in practice |
| `result` | TEXT | NOT NULL | Final or intermediate pipeline result |
| `started_at` | TEXT | NOT NULL, default local now with milliseconds | Pipeline start time |
| `completed_at` | TEXT | nullable | Pipeline completion time; NULL while pending/waiting_auth |
| `latency_ms` | INTEGER | | End-to-end pipeline latency in milliseconds, finalized when command completes |
| `error_message` | TEXT | | Populated only on failure when possible |

#### `execution_status` values

Seven values occur in practice. They fall into three groups, and **anything
computing statistics from this column needs the grouping, not a `= 'success'`
test**:

| Value | Group | Meaning |
|---|---|---|
| `success` | succeeded | Hardware action completed |
| `scheduled` | succeeded | Delayed command accepted and queued into `schedule` |
| `clarify` | pending | Waiting for the user to supply a missing slot |
| `waiting_auth` | pending | Handed to `AuthService`, not yet closed |
| `registry_request` | pending | Device or room not present in `device_registry.json` |
| `rejected` | failed | Face auth denied, or policy refused the command |
| `failed` | failed | Authenticated, but the hardware did not respond |

Counting the pending group as failures understates the success rate
substantially — on a development database of 568 rows it reported 67.5% where the
real figure was around 86%. A multi-turn clarification is the system working
correctly, not failing.

`api_command_log_summary` in `web_dashboard/app.py` therefore excludes pending
rows from the denominator entirely, and defines the failed group by **negation**
so that any status added later lands there rather than silently vanishing from
the totals. The invariant to preserve:

```text
succeeded + failed + pending == total
```

> Rows stuck at `waiting_auth` indicate an auth session that opened and never
> closed — the process was interrupted mid-scan, or the camera hung. `AuthService`
> owns closing that row (see `Integration-Contracts.md`, Contract B Step 4); a
> caller that implements its own auth flow instead of delegating will leak these.
> Normal in development data; worth investigating if it appears in a clean run.

#### `result` values

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
→ execution_status = waiting_auth      ← closed later by AuthService
→ result = waiting_auth

"bật máy lạnh phòng bếp"
→ validation_status = passed
→ execution_status = rejected
→ result = rejected: unknown_device
```

> **`latency_ms` must never stay NULL on a closed row.** A NULL there means some
> branch exited without closing the log. It is the cheapest single check on the
> whole pipeline:
>
> ```sql
> SELECT COUNT(*) FROM command_log
> WHERE execution_status NOT IN ('clarify','waiting_auth','registry_request')
>   AND latency_ms IS NULL;
> ```

---

### `face_log`

Records every face-authentication event, optionally linked to a command.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `timestamp` | TEXT | NOT NULL, default local now | Creation time |
| `command_id` | INTEGER | FK → `command_log.id`, nullable | Related command, nullable for manual tests |
| `person_name` | TEXT | NOT NULL | Recognized person, or `unknown` for a stranger |
| `confidence` | REAL | NOT NULL | Recognition score in `[0.0, 1.0]` |
| `status` | TEXT | NOT NULL | `authorized`, `denied`, `no_face`, `timeout` |
| `triggered_by` | TEXT | | Reason for auth, e.g. `voice_command`, `manual_test`, `dashboard_unlock` |
| `device` | TEXT | | Snapshot of target device |
| `room` | TEXT | | Snapshot of target room |
| `action_result` | TEXT | | `success`, `rejected`, `failed` |
| `snapshot_path` | TEXT | | **Filename** of the captured frame, e.g. `auth_20260821_234012_019.jpg`. NOT a full path. NULL if not stored |

Relationship:

```text
command_log 1 ─── 0..* face_log
```

A command can have zero or multiple face-auth attempts.

#### Every scan is recorded, including rejections

`person_name` is `NOT NULL`, so a stranger is written as `"unknown"` rather than
being skipped. Without that row every rejection would disappear from history —
which is exactly the row you want most when investigating an incident.

`confidence` is preserved even when the person was not identified, so the audit
trail can record *"recognised with 0.94 confidence that this is a stranger"*.

> **`status = 'denied'` has two distinct causes.** Either the model classified a
> stranger (`person_name = 'unknown'`, confidence possibly *high*), or it
> recognised a known person below `FACE_AUTH_THRESHOLD`. Read `person_name`
> before explaining *why* a scan was denied. Reporting only "confidence below
> threshold" — as the dashboard once did, on a row showing 87% against an 80%
> threshold — sends people to lower the threshold on a door lock to fix something
> that was never a threshold problem.

#### About `snapshot_path`

The column holds a **filename only** — `auth_20260821_234012_019.jpg`, not
`D:\253\DADN\YoloHome-AIoT\data\snapshots\auth_...jpg`. The database gets copied
between machines during development; an absolute path breaks the moment it moves,
a filename does not. Files resolve against `settings.SNAPSHOTS_DIR`
(`data/snapshots/`), and the dashboard serves them at `/snapshots/<filename>`.

Written by `AuthService`, which captures the frame **before** running
recognition, so a crash inside the recogniser still leaves evidence.

`NULL` means one of two things:

1. The row predates the feature — rows written before 2026-08-22.
2. Saving failed: disk full, no write permission, encode error. Check `error_log`
   with `module = 'face'`.

Saving is fail-soft by design and never affects the authorisation decision. The
dashboard renders a grey placeholder for `NULL` and a `?` tile when a filename is
recorded but the file is missing from disk, so the two cases stay visually
distinct.

> **`data/snapshots/` is gitignored and must stay that way.** These are biometric
> images of real people, and Git retains committed files permanently — deleting
> them later does not remove them from history. Verify with
> `git status --short data/` before committing.

The directory only grows; nothing prunes it. At roughly 50–100 KB per scan this is
harmless for a coursework project, but a long-running deployment would need a
retention policy.

> **Path encoding caveat.** `cv2.imwrite()` opens files through the ANSI system
> API and cannot write to a path containing non-cp1252 characters. On a project
> folder with Vietnamese diacritics every snapshot silently fails — `imwrite`
> returns `False` rather than raising. See `docs/Setup-Windows.md` §0.

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

> **The four sensor names are a contract**, not a convention. `RuleService`
> matches `automation_rules.sensor` against exactly `temperature`, `humidity`,
> `light`, `motion`. Renaming one when wiring real hardware makes **every**
> automation rule stop firing — with no error, no log, and no failing test. See
> `Integration-Contracts.md`, Contract C.

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

> Rules fire automatically, with nobody standing at the camera. `RuleObserver`
> therefore refuses to execute any rule whose command requires face
> authentication, and it carries its own guard rather than relying on the caller.

---

### `schedule`

Stores one-shot scheduled commands to be executed at a future datetime.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Row identifier |
| `run_at` | TEXT | NOT NULL | Target datetime as `YYYY-MM-DD HH:MM:SS` |
| `json_cmd` | TEXT | NOT NULL | JSON-serialized command payload |
| `is_active` | INTEGER | NOT NULL, default 1 | Set to 0 once fired |

A command that lands here closes its `command_log` row with
`execution_status = 'scheduled'`, which counts as a **success**: the request was
understood and accepted. Whether it later executes is a separate event.

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
security_policy
dashboard
```

This table is where fail-soft paths leave their trace, which makes it the first
place to look when something "works" but produces nothing. Two examples seen in
practice:

```sql
-- Snapshots not being written
SELECT * FROM error_log WHERE module = 'face' ORDER BY id DESC LIMIT 5;

-- The LLM tried to bypass a security requirement and was overruled
SELECT * FROM error_log WHERE module = 'security_policy' ORDER BY id DESC LIMIT 5;
```

The second is worth reading periodically even when nothing seems wrong — it
records every time `enforce_policy()` overrode the model, including attempts like
*"mở cửa chính không cần xác thực"*.

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
    snapshot_path: str | None = None,     # filename only, not a path
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

> `confidence` must be a plain Python `float` in `[0.0, 1.0]`. `predict_proba()`
> returns `numpy.float64`, which SQLite refuses to bind — and it would fail
> precisely when someone is standing at the camera. `AuthService._clean_confidence()`
> handles the conversion, including NaN.

### `services.rule_service`

```python
from services.rule_service import RuleService

rule_service = RuleService()
rule_id = rule_service.create_rule(command_id, command)
active_rules = rule_service.get_active_rules()
actions = rule_service.evaluate_sensor_data({"temperature": 35})
```

---

## Inspecting the database

The file is plain SQLite; any client works. For quick checks from PowerShell:

```powershell
# Recent commands
python -c "import sqlite3; c=sqlite3.connect('database/yolohome.db'); [print(r) for r in c.execute('SELECT id,transcript,intent,execution_status,latency_ms FROM command_log ORDER BY id DESC LIMIT 15')]"

# Recent face-auth events
python -c "import sqlite3; c=sqlite3.connect('database/yolohome.db'); [print(r) for r in c.execute('SELECT id,person_name,status,snapshot_path FROM face_log ORDER BY id DESC LIMIT 10')]"

# Distribution of execution_status
python -c "import sqlite3; c=sqlite3.connect('database/yolohome.db'); [print(r) for r in c.execute('SELECT execution_status, COUNT(*) FROM command_log GROUP BY 1 ORDER BY 2 DESC')]"
```

Note the quoting: in PowerShell, single quotes inside a double-quoted `python -c`
work, but escaped double quotes do not. For anything longer, use a here-string
(`@" ... "@`) or write a temporary `.py` file.

> **Development data mixes eras.** A database that has survived several
> development phases will contain rows from the mock LLM alongside rows from the
> real one — visible as a large gap in `latency_ms` (roughly 100 ms versus
> 1500 ms). Filter by date before quoting any figure in a report, or start from a
> clean database:
>
> ```powershell
> Move-Item database\yolohome.db database\yolohome_dev.db
> python -m database.init_db
> ```

---

## Notes

- LLM does not execute hardware directly.
- LLM returns structured command data and `next_step`.
- `waiting_auth` is generated by `CommandService`, not by the LLM.
- `command_log.result` is the pipeline/result convention used by Database, Dashboard, and reporting.
- `schema.sql` should be treated as the source of truth for table definitions.
- The runtime `.db` file is gitignored, as are `data/snapshots/` and `models/*`.
  A fresh clone therefore starts with an empty database and no model weights;
  see `models/README.md` and `.env.example`.