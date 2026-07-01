# Tổng quan cấu trúc project YoloHome-AIoT

Project này chia theo kiến trúc module hóa. Mỗi phần phụ trách một lớp riêng trong pipeline:

```
Voice/Text Input
→ STT
→ LLM Command Parser
→ Validator / Safety Layer
→ Face Auth nếu cần
→ Hardware Gateway
→ Database Logging
→ Flask Dashboard
```

Mục tiêu là mỗi thành viên có thể làm module riêng nhưng vẫn tích hợp được thông qua cùng một command schema.

---

## 1. Root folder

```
YoloHome-AIoT/
├── README.md
├── README-vi.md
├── requirements.txt
├── .env.example
├── .gitignore
├── docker-compose.yml
```

### README.md

Tài liệu chính bằng tiếng Anh. Dùng để giới thiệu project, cách cài môi trường, cách chạy, branch workflow và quy tắc làm việc nhóm. README hiện mô tả project là hệ thống nhà thông minh đa phương thức gồm FaceID, Voice Recognition, LLM và Yolo:Bit Hardware.

### README-vi.md

Phiên bản tiếng Việt của README. File này nên viết cho các thành viên trong nhóm đọc nhanh, đặc biệt là phần setup, cấu trúc folder và workflow Git.

### requirements.txt

Danh sách thư viện Python cần cài. Ví dụ: Flask, python-dotenv, MQTT, pyserial, OpenCV, dlib, face_recognition, scikit-learn, numpy, thư viện LLM, pytest.

### .env.example

File mẫu cho biến môi trường. File này được push lên GitHub để mọi người biết cần tạo `.env` như thế nào.

Ví dụ:

```
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
USE_MOCK_LLM=true

ADAFRUIT_IO_USERNAME=your_adafruit_username
ADAFRUIT_IO_KEY=your_adafruit_key

DATABASE_URL=sqlite:///database/yolohome.db
FLASK_ENV=development
FACE_AUTH_THRESHOLD=0.80
```

### .gitignore

Chặn các file không nên push lên GitHub như `.env`, `venv/`, database `.db`, model AI, dataset, ảnh webcam, audio, cache Python.

### docker-compose.yml

Hiện tại có thể để placeholder. Sau này nếu muốn chạy dashboard/database bằng Docker thì dùng file này.

---

## 2. config/

```
config/
├── __init__.py
├── settings.py
├── device_registry.json
└── command_schema.json
```

Folder này chứa cấu hình chung toàn hệ thống.

### config/settings.py

Đây là file trung tâm để quản lý path, API key, database path và runtime config. Nó load `.env`, định nghĩa `ROOT_DIR`, `DEVICE_REGISTRY_PATH`, `COMMAND_SCHEMA_PATH`, `DB_PATH`, `PROMPT_TEMPLATE_PATH`, Gemini key/model, Adafruit key, `USE_MOCK_LLM`, `FACE_AUTH_THRESHOLD`.

Các module khác nên import config từ đây thay vì tự hard-code path.

Ví dụ:

```python
from config.settings import DB_PATH, GEMINI_API_KEY, DEVICE_REGISTRY_PATH
```

### config/device_registry.json

Định nghĩa danh sách phòng, thiết bị và action hợp lệ.

Ví dụ:

```json
{
  "living_room": {
    "light": ["turn_on", "turn_off", "get_status"],
    "fan": ["turn_on", "turn_off", "get_status"]
  },
  "bedroom": {
    "light": ["turn_on", "turn_off", "get_status"],
    "fan": ["turn_on", "turn_off", "get_status"]
  },
  "main_door": {
    "door": ["open", "close", "get_status"]
  }
}
```

File này được dùng bởi LLM validator và system integration để kiểm tra command có hợp lệ không.

### config/command_schema.json

Định nghĩa contract JSON command mà LLM phải trả về.

Ví dụ command chuẩn:

```json
{
  "intent": "control_device",
  "action": "turn_on",
  "device": "light",
  "room": "living_room",
  "face_auth": false,
  "condition": null,
  "response": "Đã bật đèn phòng khách."
}
```

File này giúp cả nhóm thống nhất field name, tránh tình trạng người dùng `face_auth`, người khác dùng `requires_auth`.

### config/__init__.py

Đánh dấu `config/` là Python package. Có thể để rỗng hoặc chỉ ghi:

```python
"""Configuration package."""
```

---

## 3. modules/

```
modules/
├── __init__.py
├── speech_recognition/
├── llm_integration/
├── face_recognition/
└── hardware_gateway/
```

Folder này chứa các module kỹ thuật chính. Mỗi module tương ứng với một thành viên hoặc một nhóm nhỏ phụ trách.

### 3.1. modules/speech_recognition/

```
modules/speech_recognition/
├── __init__.py
└── stt_module.py
```

**Chức năng**

Module này nhận audio từ microphone hoặc file audio, sau đó chuyển thành transcript tiếng Việt.

**stt_module.py**

Nên có hàm chính:

```python
def transcribe(audio_input) -> str:
    ...
```

Output của module này là text:

```
"bật đèn phòng khách"
```

Text này sẽ được chuyển sang `modules/llm_integration/llm_module.py`.

**Quan hệ với module khác**

```
stt_module.transcribe()
→ transcript
→ llm_module.parse_and_validate(transcript)
```

### 3.2. modules/llm_integration/

```
modules/llm_integration/
├── __init__.py
├── llm_module.py
├── validator.py
├── prompt_template.txt
└── README_LLM.md
```

Chức năng chính là biến câu lệnh tiếng Việt thành JSON command có cấu trúc.

**llm_module.py**

File chính của LLM module.

Nhiệm vụ:

1. Load device registry
2. Build prompt từ `prompt_template.txt`
3. Gọi Gemini hoặc mock parser
4. Extract JSON từ response
5. Normalize command
6. Gọi validator
7. Trả về result object cho `system_core`

Hiện file này có các hàm quan trọng:

```
load_json()
load_device_registry()
build_prompt()
extract_json_object()
mock_parse_command()
parse_command()
parse_and_validate()
```

`parse_and_validate()` là hàm quan trọng nhất để bên System gọi. Nó trả về object gồm `ok`, `transcript`, `command`, `validation`, `latency_ms`, `log_result`, `error`. File cũng ghi rõ module này không execute hardware, mà System Integration sẽ quyết định Face Auth, execution và DB logging.

Ví dụ output:

```json
{
  "ok": true,
  "transcript": "mở cửa chính",
  "command": {
    "intent": "control_device",
    "action": "open",
    "device": "door",
    "room": "main_door",
    "face_auth": true,
    "condition": null,
    "response": "Cần xác thực khuôn mặt trước khi mở cửa."
  },
  "validation": {
    "passed": true,
    "code": "valid",
    "message": "Command schema and device are valid."
  },
  "latency_ms": 15,
  "log_result": null,
  "error": null
}
```

**validator.py**

File kiểm tra JSON command có đúng schema và có an toàn không.

Nhiệm vụ:

1. Kiểm tra đủ field bắt buộc
2. Kiểm tra intent hợp lệ
3. Kiểm tra room/device/action có trong `device_registry` không
4. Chặn lệnh nguy hiểm nếu sai rule
5. Map validation error sang result dùng cho database

Validator hiện định nghĩa các field bắt buộc như `intent`, `action`, `device`, `room`, `face_auth`, `condition`, `response`; đồng thời chặn trường hợp mở cửa mà không yêu cầu `face_auth=True`.

Ví dụ:

```
door + open + face_auth=false
→ safety_rule_violation
```

**prompt_template.txt**

Prompt chính cho Gemini. File này mô tả vai trò của LLM, schema output, device registry, sensor data và few-shot examples.

LLM phải trả về JSON only, không markdown, không giải thích.

**README_LLM.md**

Tài liệu riêng cho module LLM. Nên ghi:

- Input
- Output
- Main functions
- Schema
- Test command
- Limitations

**modules/llm_integration/__init__.py**

Nên để rỗng hoặc chỉ ghi:

```python
"""LLM integration package."""
```

Không nên import ngược `llm_module.py` ở đây, vì khi chạy:

```
python -m modules.llm_integration.llm_module
```

có thể gây warning do module bị import trước khi execute.

### 3.3. modules/face_recognition/

```
modules/face_recognition/
├── __init__.py
├── face_module.py
└── train_face_model.py (optional)
```

**Chức năng**

Module này xác thực khuôn mặt bằng webcam/camera trước khi thực hiện lệnh nhạy cảm, ví dụ mở cửa.

**face_module.py**

Nên có hàm chính:

```python
def verify_face(frame=None) -> dict:
    ...
```

Output đề xuất:

```python
{
    "person_name": "member_1",
    "confidence": 0.91,
    "authorized": True
}
```

Hoặc với người lạ:

```python
{
    "person_name": "Unknown",
    "confidence": 0.34,
    "authorized": False
}
```

**Quan hệ với module khác**

```
LLM command có face_auth=true
→ system_core/main.py gọi face_module.verify_face()
→ nếu authorized=True thì cho hardware execute
→ log_face() ghi vào face_log
```

**train_face_model.py** 

Dùng để train model nhận diện khuôn mặt, ví dụ tạo embeddings và train SVM classifier.

### 3.4. modules/hardware_gateway/

```
modules/hardware_gateway/
├── __init__.py
├── hardware_module.py
├── mqtt_client.py (optional)
└── serial_client.py (optional)
```

**Chức năng**

Module này là cầu nối giữa backend Python và thiết bị thật: Yolo:Bit, Adafruit IO, MQTT, Serial.

**hardware_module.py**

Nên có API cấp cao:

```python
def execute_command(command: dict) -> dict:
    ...
```

Input là JSON command từ LLM đã qua validation.

Ví dụ:

```json
{
    "action": "turn_on",
    "device": "light",
    "room": "living_room"
}
```

Output:

```json
{
    "status": "success",
    "message": "Light turned on"
}
```

**mqtt_client.py**

Xử lý publish/subscribe MQTT với Adafruit IO hoặc broker khác.

Ví dụ:

```
publish light command
subscribe sensor data
```

**serial_client.py**

Nếu nhóm dùng Serial với Yolo:Bit thì file này xử lý đọc/ghi qua COM port.

Nếu nhóm dùng hoàn toàn MQTT/Adafruit thì file này có thể để mock hoặc chưa dùng.

---

## 4. services/

```
services/
├── __init__.py
├── command_service.py
├── auth_service.py
├── logging_service.py
└── rule_service.py
```

Folder này chứa business logic cấp hệ thống. Nó không thuộc riêng LLM, Face hay Hardware. Nó là lớp kết nối các module lại với nhau.

### command_service.py

Điều phối xử lý một command end-to-end.

Nên có hàm:

```python
def handle_command(transcript: str) -> dict:
    ...
```

Logic:

```
transcript
→ llm_module.parse_and_validate()
→ nếu reject/clarify thì trả response
→ nếu cần face_auth thì gọi auth_service
→ nếu pass thì gọi hardware_module.execute_command()
→ gọi logging_service.log_command()
→ trả kết quả cho dashboard
```

### auth_service.py

Quản lý logic xác thực.

Nó không trực tiếp train face model, mà gọi `face_module.verify_face()`.

Nên có hàm:

```python
def require_face_auth(command: dict) -> dict:
    ...
```

Logic:

```
command.face_auth = true
→ gọi face_module.verify_face()
→ nếu authorized thì pass
→ nếu Unknown/low confidence thì reject
→ logging_service.log_face()
```

### logging_service.py

Đây là API ghi database cho toàn hệ thống.

Nó thuộc Database/System Integration, không thuộc riêng LLM.

Nên có các hàm:

```
log_command()
log_face()
log_error()
add_schedule()
get_due_schedules()
deactivate_schedule()
```

Quan hệ:

```
system_core / services
→ logging_service
→ SQLite database
→ Flask dashboard đọc data
```

`db_schema.md` hiện mô tả database có 4 bảng: `command_log`, `face_log`, `schedule`, `error_log`; trong đó `command_log` lưu mọi voice command qua STT + LLM, `face_log` lưu mỗi lần nhận diện khuôn mặt, `schedule` lưu automation job, và `error_log` lưu lỗi hệ thống.

### rule_service.py

Quản lý automation rule.

Ví dụ:

- Nếu nhiệt độ > 30 thì bật quạt
- 22:00 thì tắt đèn
- Nếu motion detected thì bật đèn

Nó có thể đọc rule từ bảng `schedule` hoặc rule riêng sau này.

---

## 5. system_core/

```
system_core/
├── __init__.py
└── main.py
```

Folder này là điểm chạy chính của toàn hệ thống.

### main.py

Đây là orchestrator/gateway chính.

Nó không nên chứa quá nhiều code chi tiết, mà nên gọi các service/module đã tách.

Pipeline gợi ý:

```python
def main():
    # initialize DB, hardware, scheduler
    # start Flask or background workers if needed
    ...
```

Hàm xử lý command có thể nằm trong `command_service.py`, còn `main.py` chỉ gọi.

Luồng chuẩn:

1. Nhận voice/text input
2. STT chuyển audio thành transcript
3. LLM parse transcript thành JSON command
4. Validator kiểm tra schema/device/action/safety
5. Nếu command cần `face_auth` → gọi Face Recognition
6. Nếu pass → gửi lệnh xuống Hardware Gateway
7. Ghi `command_log`, `face_log`, `error_log`
8. Dashboard đọc DB để hiển thị

---

## 6. database/

```
database/
├── schema.sql
├── init_db.py
├── README_DB.md
└── yolohome.db   # không push GitHub
```

Folder này thuộc bạn phụ trách Database/System.

### schema.sql

Chứa SQL tạo bảng.

Các bảng chính:

- `command_log`
- `face_log`
- `schedule`
- `error_log`

`db_schema.md` mô tả `command_log` dùng để record mọi voice command đi qua STT → LLM pipeline, gồm transcript, JSON command và result. `face_log` lưu mọi lần nhận diện khuôn mặt, gồm người được nhận diện hoặc "Unknown" và confidence dạng float 0.0–1.0.

### init_db.py

Script khởi tạo database từ `schema.sql`.

Chạy:

```
python database/init_db.py
```

Nó tạo file:

```
database/yolohome.db
```

### README_DB.md

Tài liệu riêng cho database: mô tả bảng, field, ý nghĩa result, cách query, cách reset DB.

### yolohome.db

File SQLite thật. Không nên push lên GitHub.

---

## 7. web_dashboard/

```
web_dashboard/
├── __init__.py
├── app.py
├── templates/
└── static/
```

Folder này là Flask Dashboard.

### app.py

Entry point của Flask app.

Nhiệm vụ:

1. Tạo Flask app
2. Định nghĩa routes
3. Gọi services/database để lấy data
4. Render dashboard pages

Các tab dashboard đã chốt:

1. Agent Console
2. Command Log
3. Face Auth Log

### templates/

Chứa HTML templates.

Nên có:

```
base.html
agent_console.html
command_log.html
face_auth_log.html
```

**base.html**

Layout chung: navbar/sidebar, CSS import, JS import.

**agent_console.html**

Hiển thị pipeline:

- Input/Text
- STT Transcript
- LLM JSON
- Validation result
- Face Auth result
- Execution result

**command_log.html**

Đọc bảng `command_log` từ SQLite.

Hiển thị:

- timestamp
- transcript
- intent
- device
- room
- face_auth
- validation_status
- execution_status
- result
- latency_ms

**face_auth_log.html**

Đọc bảng `face_log`.

Hiển thị:

- timestamp
- person_name
- confidence
- status
- triggered_by
- device
- room
- action_result

### static/

Chứa CSS/JS/images.

```
static/css/
static/js/
```

---

## 8. tests/

```
tests/
├── test_validator.py
├── test_llm_module.py
└── test_command_pipeline.py
```

Folder này dùng để test từng phần.

### test_validator.py

Test `validator.py`.

Kiểm tra:

- valid command → passed
- unknown device → rejected
- door open without face_auth → safety_rule_violation

### test_llm_module.py

Test mock LLM.

Kiểm tra:

- "mở cửa chính" → door/open/face_auth=true
- "bật máy lạnh phòng bếp" → reject

### test_command_pipeline.py

Test pipeline LLM → logging DB.

File này chỉ chạy khi đã có:

- `services/logging_service.py`
- `database/schema.sql`
- `database/init_db.py`
- `database/yolohome.db`

Nếu database owner chưa làm xong thì tạm chưa cần chạy file này.

---

## 9. data/

```
data/
├── sample/
└── .gitkeep
```

Folder này chứa dữ liệu mẫu hoặc dữ liệu test nhẹ.

Không nên push dataset thật, audio thật, ảnh webcam thật lên GitHub.

Nên dùng:

```
data/sample/
```

cho file mẫu nhỏ, còn dataset thật để local.

---

## 10. models/

```
models/
└── .gitkeep
```

Chứa model weights hoặc classifier sau khi train.

Ví dụ:

```
face_svm.pkl
face_encoder.joblib
pho_whisper_model/
```

Không push model thật lên GitHub vì file lớn.

---

## 11. docs/

```
docs/
├── architecture.md
├── api_contract.md
├── database_design.md
└── demo_script.md
```

Folder này chứa tài liệu báo cáo/tích hợp.

### architecture.md

Mô tả kiến trúc tổng thể:

```
STT → LLM → Validation → Face Auth → Hardware → DB → Dashboard
```

### api_contract.md

Rất quan trọng. File này mô tả input/output giữa các module.

Ví dụ contract LLM:

```
parse_and_validate(transcript: str) -> dict
```

Output:

```json
{
  "ok": true,
  "transcript": "...",
  "command": {...},
  "validation": {...},
  "latency_ms": 15,
  "log_result": null,
  "error": null
}
```

### database_design.md

Mô tả bảng SQLite, field, ví dụ dữ liệu, cách query.

### demo_script.md

Script demo cuối kỳ:

1. Bật đèn bằng text input
2. Tắt quạt phòng ngủ
3. Mở cửa chính → yêu cầu Face Auth
4. Người lạ bị reject
5. Xem Command Log
6. Xem Face Auth Log

---

## Mối liên hệ giữa các file quan trọng

### Luồng 1: Điều khiển bằng giọng nói/text

```
modules/speech_recognition/stt_module.py
→ modules/llm_integration/llm_module.py
→ modules/llm_integration/validator.py
→ services/command_service.py
→ modules/hardware_gateway/hardware_module.py
→ services/logging_service.py
→ database/yolohome.db
→ web_dashboard/app.py
```

### Luồng 2: Mở cửa cần xác thực khuôn mặt

```
User: "mở cửa chính"
→ llm_module.py trả command face_auth=true
→ validator.py kiểm tra door/open hợp lệ
→ auth_service.py gọi face_module.py
→ face_module.py trả authorized/unknown
→ nếu authorized thì hardware_module.py mở cửa
→ logging_service.py ghi command_log + face_log
→ dashboard hiển thị Face Auth Log
```

### Luồng 3: Dashboard hiển thị log

```
web_dashboard/app.py
→ đọc SQLite qua logging_service/database helper
→ render command_log.html
→ render face_auth_log.html
```

### Luồng 4: Lịch tự động

```
rule_service.py hoặc dashboard tạo schedule
→ logging_service.add_schedule()
→ system_core/main.py polling due schedules
→ command_service.py execute command
→ logging_service.deactivate_schedule()
→ command_log ghi transcript="scheduled"
```

---

## Chia ownership cho nhóm

**STT owner:**
```
modules/speech_recognition/
```

**LLM owner:**
```
modules/llm_integration/
config/command_schema.json
config/device_registry.json
tests/test_validator.py
tests/test_llm_module.py
tests/test_command_pipeline.py
```

**Face owner:**
```
modules/face_recognition/
```

**Hardware owner:**
```
modules/hardware_gateway/
```

**Database/System owner:**
```
database/
services/logging_service.py
services/command_service.py
services/auth_service.py
services/rule_service.py
system_core/
```

**Dashboard owner:**
```
web_dashboard/
templates/
static/
```

---

**Chốt ngắn gọn:** `modules/` xử lý chuyên môn từng phần, `services/` nối các phần lại, `database/` lưu log, `system_core/` chạy pipeline tổng, `web_dashboard/` đọc DB để hiển thị.
