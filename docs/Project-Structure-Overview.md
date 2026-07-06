# Tổng quan cấu trúc project YoloHome-AIoT

Project YoloHome-AIoT được tổ chức theo kiến trúc module hóa. Mỗi module phụ trách một phần riêng trong pipeline nhà thông minh đa phương thức:

```text
Voice/Text Input
→ STT
→ LLM Command Parser
→ Validator / Safety Layer
→ Face Auth nếu cần
→ Command Service
→ Hardware Gateway
→ Database Logging
→ Flask Dashboard
```

Mục tiêu là mỗi thành viên có thể phát triển module riêng nhưng vẫn tích hợp được thông qua cùng một JSON command schema, device registry và các service trung tâm.

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
├── modules/
├── services/
├── system_core/
├── tests/
└── web_dashboard/
```

Các file runtime như `.env`, `venv/`, `.pytest_cache/`, `__pycache__/`, `database/yolohome.db`, model AI và dataset thật không nên commit lên GitHub.

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

Folder này chứa cấu hình chung cho toàn hệ thống.

### `settings.py`

Quản lý đường dẫn, biến môi trường và runtime config:

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
FACE_AUTH_THRESHOLD
```

Các module khác nên import từ `config.settings` thay vì tự hard-code path.

### `device_registry.json`

Định nghĩa phòng, thiết bị và action được hỗ trợ thật.

Ví dụ:

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

Validator dùng file này để kiểm tra command có được phép execute không.

### `command_schema.json`

Định nghĩa contract JSON command mà LLM phải trả về:

```text
required_fields
intents
valid_sensors
valid_operators
sensitive_actions
```

File này giúp thống nhất output giữa LLM, validator, command service và dashboard.

### `language_aliases.json`

Chứa alias tiếng Việt cho mock parser.

Ví dụ:

```text
"phòng khách" → living_room
"đèn" → light
"bật" → turn_on
"tắt" → turn_off
```

Nhờ đó có thể mở rộng alias mà không cần sửa logic parser trong Python.

### `device_capabilities.json` và `capabilities.py`

Định nghĩa capability và safety policy cho command factory.

Ví dụ:

```text
switch       → turn_on / turn_off
cover        → open / close
entry_point  → open cần face_auth
```

`capabilities.py` đọc file JSON này và cung cấp helper để command factory/validator tra cứu policy.

---

## 3. modules/

```text
modules/
├── face_recognition/
├── hardware_gateway/
├── llm_integration/
└── speech_recognition/
```

Folder này chứa các module kỹ thuật chính.

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

Module này biến câu lệnh tiếng Việt thành JSON command đã được validate.

### `llm_module.py`

Nhiệm vụ chính:

```text
- Load device registry
- Load command schema
- Build prompt từ prompt_template.txt
- Gọi Gemini hoặc mock parser
- Extract JSON từ model response
- Normalize command
- Validate command
- Trả về next_step cho CommandService
```

Hàm quan trọng nhất:

```python
parse_and_validate(transcript: str, sensor_data: dict | None = None, use_mock: bool | None = None) -> dict
```

Output chuẩn gồm:

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

Module LLM không trực tiếp điều khiển phần cứng. Nó chỉ hiểu command và trả quyết định tiếp theo cho service layer.

### `validator.py`

Validator là safety gate trước khi command được execute.

Nó kiểm tra:

```text
- required fields
- intent hợp lệ
- room/device/action trong device_registry
- condition của automation rule
- sensitive action
- capability safety policy
```

### `llm_strategy.py`

Chứa implementation của Strategy Pattern cho LLM:

```text
GeminiLLMStrategy
MockLLMStrategy
```

`CommandService` dùng `LLMStrategy` thay vì gọi trực tiếp Gemini.

### `prompt_template.txt`

Prompt cho Gemini. Prompt yêu cầu model trả JSON only, dùng đúng registry, schema và context sensor hiện tại.

---

## 3.2. modules/speech_recognition/

```text
modules/speech_recognition/
├── __init__.py
└── stt_module.py
```

Module này phụ trách Speech-to-Text. Output là transcript text để chuyển sang LLM pipeline.

Ví dụ:

```text
audio input
→ "bật đèn phòng khách"
→ llm_module.parse_and_validate(...)
```

---

## 3.3. modules/face_recognition/

```text
modules/face_recognition/
├── __init__.py
└── face_module.py
```

Module này xác thực khuôn mặt cho các command nhạy cảm như mở cửa chính.

Output đề xuất:

```python
{
    "person_name": "member_1",
    "confidence": 0.91,
    "authorized": True
}
```

---

## 3.4. modules/hardware_gateway/

```text
modules/hardware_gateway/
├── __init__.py
└── hardware_module.py
```

Module này là adapter giữa backend Python và thiết bị thật như Yolo:Bit, Serial, MQTT hoặc Adafruit IO.

API cấp cao nên là:

```python
execute_command(command: dict) -> dict
```

---

## 4. services/

```text
services/
├── __init__.py
├── auth_service.py
├── command_service.py
├── logging_service.py
└── rule_service.py
```

Folder này chứa business logic và orchestration giữa các module.

### `command_service.py`

Điều phối command pipeline.

Vai trò chính:

```text
- Nhận transcript
- Gọi LLMStrategy để parse + validate
- Route theo next_step
- Tạo Command object bằng command factory
- Execute command qua HardwareReceiver
- Lưu command history để undo nếu cần
- Ghi log command lifecycle
```

### `auth_service.py`

Quản lý flow xác thực. Service này gọi face module và quyết định command có được execute tiếp không.

### `logging_service.py`

Ghi log vào SQLite:

```text
command_log
face_log
sensor_log
error_log
schedule
```

### `rule_service.py`

Quản lý automation rule như:

```text
nếu nhiệt độ > 30 thì bật quạt phòng khách
```

---

## 5. system_core/

```text
system_core/
├── __init__.py
├── commands.py
├── main.py
├── observer.py
└── strategies.py
```

Folder này chứa core abstraction và design pattern.

### `strategies.py`

Định nghĩa Strategy interface:

```text
STTStrategy
LLMStrategy
```

### `commands.py`

Định nghĩa Command Pattern:

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

### `observer.py`

Định nghĩa Observer Pattern nếu sensor/hardware flow cần publish event:

```text
Subject
Observer
SensorSubject
```

### `main.py`

Entry point hoặc orchestrator chính của hệ thống.

---

## 6. database/

```text
database/
├── __init__.py
├── init_db.py
├── schema.sql
├── README_DB.md
└── yolohome.db   # runtime file, không commit
```

Database dùng SQLite.

Các bảng chính:

```text
command_log
face_log
sensor_log
schedule
error_log
automation_rules
```

Chi tiết schema nằm trong:

```text
database/README_DB.md
```

Chạy khởi tạo DB:

```bash
python -m database.init_db
```

---

## 7. web_dashboard/

```text
web_dashboard/
├── __init__.py
├── app.py
├── templates/
└── static/
```

Flask dashboard dùng để hiển thị pipeline và log.

Các tab chính:

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
├── pattern/
├── service/
├── test_config.py
└── test_runtime_config.py
```

Test được nhóm theo module hoặc kiến trúc.

### `tests/llm/`

Kiểm tra:

```text
- prompt building
- mock parser
- validator
- schema-driven condition validation
- language aliases
- capability validation
```

### `tests/pattern/`

Kiểm tra:

```text
- Command Pattern
- Strategy Pattern
- Command factory
- execute / undo behavior
```

### `tests/db/`

Kiểm tra:

```text
- database schema
- sensor logging
```

### `tests/service/`

Kiểm tra:

```text
- command pipeline
- command service behavior
```

Chạy toàn bộ test:

```bash
python -m pytest -q
```

---

## 9. docs/

```text
docs/
├── Project-Structure-Overview.md
└── LLM-Command-Strategy-Overview.md
```

### `Project-Structure-Overview.md`

Tổng quan cấu trúc folder, vai trò module và luồng tích hợp.

### `LLM-Command-Strategy-Overview.md`

Tài liệu riêng cho LLM command pipeline, Strategy Pattern và Command Pattern.

---

## 10. diagrams/

```text
diagrams/
├── Class-Diagram.png
├── CommandPattern.png
├── ObserverPattern.png
└── StrategyPattern.png
```

Chứa sơ đồ kiến trúc và design pattern dùng cho báo cáo.

---

## 11. Các luồng chính

### Luồng 1: Điều khiển bằng text/voice

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

### Luồng 2: Command cần xác thực khuôn mặt

```text
User: "mở cửa chính"
→ LLM trả command face_auth=true
→ Validator xác nhận door.open là sensitive action
→ CommandService trả next_step=auth_required
→ AuthService/FaceModule xác thực
→ execute_authorized_command()
→ HardwareGateway execute
→ LoggingService ghi command_log + face_log
```

### Luồng 3: Automation rule

```text
User: "nếu nhiệt độ trên 30 độ thì bật quạt phòng khách"
→ LLM trả intent=create_rule
→ Validator kiểm tra condition
→ RuleService lưu automation rule
→ LoggingService ghi command_log
```

---

## 12. Ownership gợi ý

### LLM owner

```text
modules/llm_integration/
config/command_schema.json
config/language_aliases.json
config/device_capabilities.json
config/capabilities.py
tests/llm/
tests/pattern/test_command_strategy_patterns.py
```

### STT owner

```text
modules/speech_recognition/
system_core/strategies.py
```

### Face owner

```text
modules/face_recognition/
services/auth_service.py
```

### Hardware owner

```text
modules/hardware_gateway/
system_core/commands.py
```

### Database/System owner

```text
database/
services/logging_service.py
services/rule_service.py
services/command_service.py
```

### Dashboard owner

```text
web_dashboard/
templates/
static/
```

---

## 13. Design Pattern summary

### Strategy Pattern

```text
LLMStrategy / STTStrategy
→ GeminiLLMStrategy / MockLLMStrategy / future STT strategy
→ CommandService dùng strategy qua interface
```

Mục tiêu: thay đổi model AI mà không sửa logic điều phối.

### Command Pattern

```text
LLM JSON command
→ create_device_command()
→ Command object
→ execute() / undo()
→ HardwareReceiver
```

Mục tiêu: chuẩn hóa hành động thiết bị thành object command, dễ execute, undo và log.

### Observer Pattern

```text
Subject
→ notify(sensor_data)
→ Observer.update(sensor_data)
```

Mục tiêu: hỗ trợ sensor event flow nếu phần hardware/rule cần publish dữ liệu cảm biến.