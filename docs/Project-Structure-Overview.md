# YoloHome-AIoT Project Structure Overview

YoloHome-AIoT is organised as a modular architecture. Each module owns one stage
of the multimodal smart-home pipeline:

```text
Voice/Text Input
→ STT
→ LLM Command Parser
→ Validator / Safety Layer
→ Face Auth (when required)
→ Command Service
→ Hardware Gateway
→ Database Logging
→ Flask Dashboard
```

The goal is that each team member can develop their own module independently
while still integrating through a shared JSON command schema, device registry and
a common set of services.

---

## 1. Root folder

```text
YoloHome-AIoT/
├── README.md
├── README-vi.md
├── requirements.txt
├── .env.example
├── .gitignore
├── docker-compose.yml
├── config/
├── database/
├── diagrams/
├── docs/
├── models/          # face_model.pkl, model_env.json
├── modules/
├── notebooks/       # Colab notebooks for Face Recognition training
├── services/
├── system_core/
├── tests/
├── tools/           # measure_llm.py, probe_gemini.py, benchmark_llm.py
└── web_dashboard/
```

Runtime files such as `.env`, `venv/`, `.pytest_cache/`, `__pycache__/`,
`database/yolohome.db`, and the real image dataset should not be committed.

---

## 2. config/

```text
config/
├── __init__.py
├── settings.py
├── capabilities.py
├── command_schema.json
├── device_registry.json
├── device_capabilities.json
└── language_aliases.json
```

System-wide configuration.

### `settings.py`

Manages paths, environment variables and runtime configuration:

```text
ROOT_DIR
CONFIG_DIR
DATABASE_DIR
DEVICE_REGISTRY_PATH
COMMAND_SCHEMA_PATH
LANGUAGE_ALIASES_PATH
DEVICE_CAPABILITIES_PATH
DB_PATH
SCHEMA_PATH
PROMPT_TEMPLATE_PATH

GEMINI_API_KEY
GEMINI_MODEL
USE_MOCK_LLM

HARDWARE_MODE            # "simulation" | "real"
ADAFRUIT_IO_USERNAME
ADAFRUIT_IO_KEY

USE_MOCK_FACE            # true -> MockFaceRecognizer; ANYONE can open the door
FACE_AUTH_THRESHOLD
FACE_REQUIRE_BLINK
FACE_SCAN_TIMEOUT_SECONDS
FACE_MODEL_PATH
CAMERA_INDEX

USE_MOCK_STT
PHOWHISPER_MODEL
VOICE_RECORD_SECONDS
```

`check_config()` prints warnings at import time for settings that are
technically valid but probably not what the user intended — for example
`USE_MOCK_FACE` enabled, `face_model.pkl` missing, or `HARDWARE_MODE=real`
without Adafruit credentials.

Other modules should import from `config.settings` rather than hard-coding paths.

### `device_registry.json`

Defines the rooms, devices and actions that are actually supported.

```json
{
  "living_room": {
    "light": ["turn_on", "turn_off", "get_status"],
    "fan": ["turn_on", "turn_off", "get_status"]
  },
  "main_door": {
    "door": ["open", "close", "get_status"]
  }
}
```

The validator uses this file to decide whether a command may execute.

### `command_schema.json`

Defines the JSON command contract the LLM must return:

```text
required_fields
intents
valid_sensors
valid_operators
sensitive_actions
```

This keeps the output aligned across the LLM, validator, command service and
dashboard.

### `language_aliases.json`

Vietnamese aliases for the mock parser, and — since slot grounding was
introduced — for **server-side validation of every slot value**.

```text
"phòng khách" → living_room
"đèn"         → light
"bật"         → turn_on
"tắt"         → turn_off
"có" / "ok"   → yes      (registry request confirmation)
"không"       → no
```

Aliases can therefore be extended without touching parser logic in Python.

### `device_capabilities.json` and `capabilities.py`

Define capabilities and the safety policy used by the command factory.

```text
switch       → turn_on / turn_off
cover        → open / close
entry_point  → open requires face_auth
```

`capabilities.py` reads this JSON and exposes helpers for the command factory and
validator to look up policy.

---

## 3. modules/

```text
modules/
├── face_recognition/
├── hardware_gateway/
├── llm_integration/
└── speech_recognition/
```

The main technical modules.

---

## 3.1. modules/llm_integration/

```text
modules/llm_integration/
├── __init__.py
├── llm_module.py
├── llm_strategy.py
├── prompt_template.txt
└── validator.py
```

Turns a Vietnamese utterance into a validated JSON command.

### `llm_module.py`

Responsibilities:

```text
- Load device registry
- Load command schema
- Build the prompt from prompt_template.txt
- Call Gemini or the mock parser
- Extract JSON from the model response
- Normalize the command
- Apply server-side safeguards (grounding, conflict resolution, unknown rooms)
- Validate the command
- Return next_step to CommandService
```

Main entry point:

```python
parse_and_validate(transcript: str, sensor_data: dict | None = None, use_mock: bool | None = None) -> dict
```

Standard output fields:

```text
ok
transcript
command
validation
next_step
latency_ms
log_result
error
```

The LLM module never drives hardware directly. It only understands the command
and hands the next decision to the service layer.

### `validator.py`

The safety gate before a command may execute. It checks:

```text
- required fields
- valid intent
- room/device/action present in device_registry
- automation rule conditions
- sensitive actions
- capability safety policy
```

`enforce_policy()` overrides `face_auth` in **both directions**, so an LLM
claiming a sensitive action needs no authentication is overruled.

### `llm_strategy.py`

The Strategy Pattern implementation for the LLM:

```text
GeminiLLMStrategy
MockLLMStrategy
```

`CommandService` depends on `LLMStrategy` rather than calling Gemini directly.

### `prompt_template.txt`

The Gemini prompt. It requires JSON-only output, using the current registry,
schema and sensor context.

---

## 3.2. modules/speech_recognition/

```text
modules/speech_recognition/
├── __init__.py
├── stt_module.py              # PhoWhisper (transformers) — default engine
├── stt_faster_whisper.py      # alternative engine (CTranslate2)
├── evaluate_model.py          # measures WER / CER / latency
├── finetune_final/            # fine-tuned checkpoint (weights via HuggingFace)
├── finetune_final_ct2/        # same checkpoint converted to CTranslate2
└── samples/                   # .wav files for evaluation (gitignored)
```

Speech-to-Text. Output is a transcript passed on to the LLM pipeline.
Both engines implement `STTStrategy` and are selected via `STT_ENGINE` in `.env`.
Comparative measurements: `docs/STT-Evaluation.md`.

```text
audio input
→ "bật đèn phòng khách"
→ llm_module.parse_and_validate(...)
```

`PhoWhisperSTT` loads its model **lazily** on the first `transcribe()` call
(~10 s on CPU), then ~1.5 s for 4 s of audio.

> **The transcript must carry Vietnamese diacritics.** Slot grounding matches
> every `room` / `device` value against `language_aliases.json`, and those
> aliases carry diacritics. An ASCII-folded transcript loses every slot. See
> `docs/Integration-Contracts.md` Contract A.

`ffmpeg` must be on PATH — without it `transcribe()` returns an empty string
silently.

---

## 3.3. modules/face_recognition/

```text
modules/face_recognition/
├── __init__.py
└── face_module.py
```

Recognises faces for sensitive commands such as opening the main door. It only
recognises and reports confidence — it does **not** decide whether to allow the
action.

Output per Contract B:

```python
{
    "person_name": "member_1",   # None if no HOUSEHOLD MEMBER was recognised
    "confidence": 0.91,          # in [0.0, 1.0]
}
```

> **Do not return an `authorized` flag.** The authorization decision belongs to
> `AuthService`, because the threshold is server configuration. If the module
> returns such a flag, `AuthService` deliberately ignores it — trusting it would
> let a swapped-in model grant itself permission. See `Design-Principles.md` §1.

The `Unknown` label from the training set is **never** returned as an identity:
`"Unknown"` is a truthy string, so the rule
`bool(person_name) and confidence >= threshold` would open the door at exactly
the moment the model correctly identifies a stranger.

---

## 3.4. modules/hardware_gateway/

```text
modules/hardware_gateway/
├── __init__.py
└── hardware_module.py
```

The adapter between the Python backend and Yolo:Bit over Adafruit IO MQTT. The
system uses **no serial port** anywhere.

High-level API:

```python
HardwareModule(mode="simulation" | "real")

execute_command(command: dict) -> dict     # {"status": "success"|"error", "state"?: ...}
read_sensors() -> dict                      # 4 CONTRACT keys: temperature, humidity, light, motion
poll_sensors() -> dict                      # read then notify observers — the rule engine's heartbeat
```

Command feeds are keyed by `(room, device, action)`, one feed per room. A key
without `room` would make "turn on the bedroom light" and "turn on the living
room light" publish to the same feed. Details in
`docs/Integration-Contracts.md` Contract C.

---

## 4. services/

```text
services/
├── __init__.py
├── auth_service.py
├── command_service.py
├── logging_service.py
├── rule_service.py
└── session_service.py
```

Business logic and orchestration between modules.

### `command_service.py`

Drives the command pipeline:

```text
- Receive the transcript
- Handle Yes/No confirmations server-side, before any LLM call
- Call LLMStrategy to parse + validate
- Route by next_step
- Build a Command object via the command factory
- Execute it through the HardwareReceiver
- Keep command history for undo
- Log the command lifecycle
```

### `auth_service.py`

The face authentication gate. It receives the handoff from `CommandService`
(`next_step="auth_required"`), compares confidence against
`FACE_AUTH_THRESHOLD`, and only then calls `execute_authorized_command()`. It is
also responsible for closing the `command_log` row and writing `face_log`.

It **does not acquire the frame itself** — the orchestrator supplies it.

### `logging_service.py`

Writes to SQLite:

```text
command_log
face_log
sensor_log
error_log
schedule
```

### `rule_service.py`

Manages automation rules such as:

```text
nếu nhiệt độ trên 30 độ thì bật quạt phòng khách
```

Rules are edge-triggered with hysteresis, so a value oscillating around the
threshold does not spam commands.

### `session_service.py`

Holds multi-turn conversation state: the pending command awaiting clarification,
and separately any pending registry request awaiting a Yes/No answer. Both have a
TTL; only the former counts against `SESSION_MAX_TURNS`.

---

## 5. system_core/

```text
system_core/
├── __init__.py
├── commands.py
├── contracts.py
├── main.py
├── observers.py
└── strategies.py
```

Core abstractions and design patterns.

### `strategies.py`

Strategy interfaces:

```text
STTStrategy
LLMStrategy
```

### `commands.py`

Command Pattern:

```text
Command
DeviceCommand
GenericDeviceCommand
OpenDoorCommand
CloseDoorCommand
GetStatusCommand
HardwareReceiver
create_device_command()
```

### `observers.py`

Observer Pattern for the sensor data flow:

```text
Subject                  # HardwareModule inherits from this
Observer                 # interface
RuleObserver             # the link from sensors to automation rules
SensorLoggingObserver    # in-memory history for the dashboard
SensorPersistObserver    # writes sensor_log to SQLite, rate-limited
```

`RuleObserver` is the **link that used to be missing**:
`RuleService.evaluate_sensor_data()` was called from nowhere, so rules stored in
the database never fired the fan — with no error and no log.

### `contracts.py`

Checks interface contracts at startup: the `LLMStrategy` signature, the presence
of `execute_command()` on the hardware receiver, and the shape of `get_status`
results. Mismatches surface at boot rather than mid-demo.

### `main.py`

The system entry point and orchestrator.

---

## 6. database/

```text
database/
├── __init__.py
├── init_db.py
├── schema.sql
├── README_DB.md
└── yolohome.db   # runtime file, not committed
```

SQLite. Main tables:

```text
command_log
face_log
sensor_log
schedule
error_log
automation_rules
```

Schema details are in `database/README_DB.md`.

```bash
python -m database.init_db
```

The schema uses `CREATE TABLE IF NOT EXISTS`, so it is idempotent — adding a new
**table** applies automatically, but adding a **column** to an existing table
does not. That case needs `ALTER TABLE` or a database reset.

---

## 7. web_dashboard/

```text
web_dashboard/
├── __init__.py
├── app.py
├── templates/
```

Flask dashboard for viewing the pipeline and logs. Main tabs:

```text
Agent Console
Command Log
Face Auth Log
```

---

## 8. tests/

```text
tests/
├── db/
├── llm/
├── modules/
│   ├── face/          # test_face_module_contract.py + manual tool test_face.py
│   ├── llm/           # slot grounding, suggestions, failure responses, registry confirmation
│   └── stt/           # manual tool test_stt.py
├── pattern/
├── service/
├── services/          # test_auth_service.py
├── test_config.py
├── test_main_orchestrator.py
└── test_runtime_config.py
```

> **Two kinds of file live under `tests/`.** Automated tests contain `assert`
> statements and run offline. Manual tools (`test_face.py`, `test_stt.py`) need a
> webcam or microphone and a person operating them — they keep every heavy
> dependency inside `main()` and set `__test__ = False`, otherwise pytest imports
> them and turns the whole suite red on a machine without dlib.

### `tests/llm/`

```text
- prompt building
- mock parser
- validator
- schema-driven condition validation
- language aliases
- capability validation
```

### `tests/modules/llm/`

```text
- slot grounding (rejecting values the user never said)
- suggestion filtering (never suggest an option that doesn't exist)
- failure responses (a failed command must not sound successful)
- room/device name collision
- registry request confirmation flow
```

### `tests/pattern/`

```text
- Command Pattern
- Strategy Pattern
- Command factory
- execute / undo behaviour
```

### `tests/db/`

```text
- database schema
- sensor logging
```

### `tests/service/` and `tests/services/`

```text
- command pipeline
- command service behaviour
- AuthService (offline, injected fake logging service)
```

```bash
python -m pytest -q
```

The entire suite runs offline: no dlib, no webcam, no microphone, no API key, no
hardware.

---

## 9. docs/

```text
docs/
├── Project-Structure-Overview.md
├── LLM-Command-Strategy-Overview.md
├── Integration-Contracts.md
├── Design-Principles.md
├── Module-Responsibilities.md
├── Face-Recognition.md
├── Setup-Windows.md
├── STT_Evaluation.md
└── Web_dashboard.md
```

| File | Contents |
|---|---|
| `Project-Structure-Overview.md` | Folder layout, module roles, integration flows |
| `LLM-Command-Strategy-Overview.md` | LLM pipeline, Strategy Pattern, Command Pattern |
| `Integration-Contracts.md` | The three seams: STT→LLM, LLM→Face Auth, LLM→Hardware |
| `Design-Principles.md` | The eight principles governing how the system is written |
| `Module-Responsibilities.md` | Who owns which file, and the boundaries not to cross |
| `Face-Recognition.md` | Model training and runtime inference |
| `Setup-Windows.md` | Fourteen traps encountered while setting up on Windows |
| `STT-Evaluation.md` | WER/CER/latency: PhoWhisper vs faster-whisper |
| `Web_dashboard.md` | Flask dashboard: routes, API endpoints, UI |

---

## 10. diagrams/

```text
diagrams/
├── Class-Diagram.png
├── CommandPattern.png
├── ObserverPattern.png
└── StrategyPattern.png
```

Architecture and design pattern diagrams used in the report.

---

## 11. Main flows

### Flow 1 — text/voice control

```text
speech_recognition/stt_module.py
→ llm_integration/llm_module.py
→ llm_integration/validator.py
→ services/command_service.py
→ system_core/commands.py
→ hardware_gateway/hardware_module.py
→ services/logging_service.py
→ database/yolohome.db
→ web_dashboard/app.py
```

### Flow 2 — a command requiring face authentication

```text
User: "mở cửa chính"
→ LLM returns a command with face_auth=true
→ Validator confirms door.open is a sensitive action
→ CommandService returns next_step=auth_required
→ MainOrchestrator.capture_frame() opens the camera, waits for a blink,
  returns a clean frame
→ AuthService.authorize_and_execute(result, frame) compares the threshold
→ execute_authorized_command() is called ONLY when authorized
→ LoggingService writes command_log + face_log
```

### Flow 3 — automation rules

```text
User: "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách"
→ LLM returns intent=create_rule
→ Validator checks the condition
→ RuleService stores the automation rule
→ LoggingService writes command_log
```

Later, on every sensor poll:

```text
HardwareModule.poll_sensors()
→ notify(sensor_data)
→ RuleObserver.update()
→ RuleService.evaluate_sensor_data()   (edge-triggered)
→ CommandService.execute_authorized_command()
```

---

## 12. Suggested ownership

### LLM owner

```text
modules/llm_integration/
config/command_schema.json
config/language_aliases.json
config/device_capabilities.json
config/capabilities.py
tests/llm/
tests/modules/llm/
tests/pattern/test_command_strategy_patterns.py
```

### STT owner

```text
modules/speech_recognition/
system_core/strategies.py
tests/modules/stt/
```

### Face owner

```text
modules/face_recognition/
services/auth_service.py
notebooks/face_recognition/
models/face_model.pkl
tests/modules/face/
tests/services/test_auth_service.py
```

### Hardware owner

```text
modules/hardware_gateway/
system_core/commands.py
system_core/observers.py
```

### Database/System owner

```text
database/
services/logging_service.py
services/rule_service.py
services/command_service.py
services/session_service.py
system_core/main.py
```

### Dashboard owner

```text
web_dashboard/
templates/
static/
```

---

## 13. Design pattern summary

### Strategy Pattern

```text
LLMStrategy / STTStrategy
→ GeminiLLMStrategy / MockLLMStrategy / PhoWhisperSTT / MockSTTStrategy
→ CommandService uses the strategy through the interface
```

Purpose: swap the AI model without touching orchestration logic.

Remove it and `CommandService` depends directly on Gemini, forcing tests to make
real API calls.

### Command Pattern

```text
LLM JSON command
→ create_device_command()
→ Command object
→ execute() / undo()
→ HardwareReceiver
```

Purpose: turn device actions into objects that are easy to execute, undo and log.

Remove it and there is no undo.

### Observer Pattern

```text
Subject
→ notify(sensor_data)
→ Observer.update(sensor_data)
```

Purpose: one sensor loop feeds several consumers (rules, logging, persistence)
without them knowing about each other.

Remove it and adding a new consumer of sensor data means modifying
`HardwareModule`.