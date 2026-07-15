# YoloHome-AIoT - Hệ Thống Nhà Thông Minh Đa Phương Thức

Chào mừng bạn đến với dự án **YoloHome-AIoT**! Kho lưu trữ này chứa mã nguồn cho hệ thống nhà thông minh đa phương thức (FaceID, Nhận diện giọng nói, LLM và phần cứng Yolo:Bit).

Vì nhóm của chúng mình gồm 5 thành viên làm việc trong các lĩnh vực hoàn toàn khác nhau (AI, Giao diện người dùng và Phần cứng), việc tuân thủ nghiêm ngặt quy trình làm việc này là **bắt buộc** để tránh xung đột tích hợp và lỗi biên dịch.

## 🗂️ Cấu trúc dự án

```text
.
├── benchmark/          # Bộ dữ liệu có nhãn để đánh giá LLM
├── config/             # Cấu hình runtime, schema, alias và capability
├── database/           # SQLite schema, script khởi tạo và tài liệu database
├── diagrams/           # Sơ đồ kiến trúc và design pattern
├── docs/               # Tài liệu dự án
├── modules/            # Các module AIoT: LLM, face, speech, hardware gateway
├── services/           # Service điều phối logic ứng dụng
├── system_core/        # Core abstraction và implementation của design pattern
├── tests/              # Unit test và integration test theo từng nhóm
├── tools/              # Script chẩn đoán & benchmark (chạy bằng python -m tools.*)
├── web_dashboard/      # Flask dashboard UI
├── .env.example        # File mẫu cho biến môi trường
├── docker-compose.yml  # Cấu hình chạy container nếu cần
├── README.md           # Tài liệu tiếng Anh
└── README-vi.md        # Tài liệu tiếng Việt
```

Pipeline lệnh LLM trải trên nhiều thư mục. Các file chính:

```text
modules/llm_integration/
├── llm_module.py         # Parser lõi: prompt, gọi Gemini, retry, routing
├── llm_strategy.py       # GeminiLLMStrategy, MockLLMStrategy
├── openai_strategy.py    # OpenAILLMStrategy (chứng minh Strategy Pattern)
├── validator.py          # Validate schema + policy face_auth phía server
└── prompt_template.txt   # Template prompt (placeholder điền từ config)

services/
├── command_service.py    # Điều phối toàn bộ pipeline transcript -> phần cứng
├── session_service.py    # Hội thoại nhiều lượt (lưu câu hỏi đang chờ)
├── rule_service.py       # Automation rule edge-triggered
├── logging_service.py    # Ghi log lệnh / lỗi vào SQLite
└── auth_service.py       # Luồng face-auth (do module Face phụ trách)

system_core/
├── commands.py           # Command Pattern (hành động thiết bị, undo, registry)
├── strategies.py         # Interface LLMStrategy
├── observers.py          # Observer Pattern: nối cảm biến -> rule
├── contracts.py          # Kiểm tra hợp đồng interface lúc khởi động
└── main.py               # Điểm vào ứng dụng

tools/
├── check_setup.py        # Kiểm tra config + DB + pipeline (offline)
├── probe_gemini.py       # Liệt kê model khả dụng với API key
├── smoke_gemini.py       # 10 câu gọi Gemini thật, kiểm tra pipeline
└── benchmark_llm.py      # Benchmark provider-agnostic, xuất Markdown
```

Các file cấu hình quan trọng:

```text
config/
├── command_schema.json       # Schema command, intent, sensor, operator
├── device_registry.json      # Phòng, thiết bị và action được hỗ trợ
├── language_aliases.json     # Alias tiếng Việt cho mock parser
├── device_capabilities.json  # Capability và safety policy cho command
├── capabilities.py           # Resolver cho capability policy
└── settings.py               # Đường dẫn runtime và biến môi trường
```

Các file runtime như `database/yolohome.db`, `__pycache__/`, `.pytest_cache/`,
`.env` và `venv/` không nên commit lên GitHub.

Tài liệu về LLM command pipeline, Strategy Pattern và Command Pattern nằm tại
[`docs/LLM-Command-Strategy-Overview.md`](docs/LLM-Command-Strategy-Overview.md).

---

## 1. Điều kiện tiên quyết & Bộ công cụ

Trước khi viết bất kỳ code nào, hãy đảm bảo máy tính cục bộ của bạn đã cài đặt những thứ sau:

1. **Python 3.10 trở lên** (Đảm bảo Python được thêm vào PATH của hệ thống).

2. **Git** (Để kiểm soát phiên bản).

3. **Visual Studio Code (VS Code)** (IDE được khuyến nghị).

4. **C++ Build Tools** (Cần thiết để biên dịch `dlib` trong mô-đun FaceID - cài đặt thông qua Visual Studio Build Tools trên Windows).

---

## 2. Thiết lập Môi trường Cục bộ

Chúng mình sẽ sử dụng Môi trường ảo Python để đảm bảo mọi người đều sử dụng cùng một phiên bản thư viện.

### 2.1 Sao chép kho lưu trữ:

```bash
git clone [https://github.com/](https://github.com/)[tên người dùng của bạn]/YoloHome-AIoT.git

cd YoloHome-AIoT

```

### 2.2 Tạo môi trường ảo:

```bash
python -m venv venv

```

### 2.3 Kích hoạt môi trường ảo:

- Windows PowerShell:
```powershell
.\venv\Scripts\Activate.ps1
```

- Windows Git Bash: 
```bash
source venv/Scripts/activate
```

- Mac/Linux:
```bash
source venv/bin/activate
```
*(Bạn sẽ thấy (venv) xuất hiện ở đầu dòng lệnh).*

### 2.4 Cài đặt các phụ thuộc của dự án:

```bash
pip install -r requirements.txt

```

### 2.5 Thiết lập môi trường Biến:

Sao chép file môi trường mẫu:

```powershell
Copy-Item .env.example .env
```

Hoặc trên macOS/Linux:

```bash
cp .env.example .env
```

Hãy hỏi thành viên nhóm phụ trách từng module về các khóa bí mật. Tạo một tệp .env trong thư mục gốc và thêm chúng vào:

```bash
# Engine LLM: giữ true để phát triển offline và chạy test (không tốn quota)
USE_MOCK_LLM=true
GEMINI_API_KEY="your_api_key"

# QUAN TRỌNG: pin model stable. Tính tới 07/2026, cả gemini-2.5-flash và
# gemini-2.5-flash-lite đều trả 404 với user mới. KHÔNG dùng alias "-latest"
# (tự hot-swap sang model mới, làm số liệu benchmark không tái lập được).
# Chạy `python -m tools.probe_gemini` để xem key của bạn dùng được model nào.
GEMINI_MODEL="gemini-3.1-flash-lite"
GEMINI_THINKING_LEVEL=minimal
GEMINI_STRUCTURED_OUTPUT=true

ADAFRUIT_IO_USERNAME="your_username"
ADAFRUIT_IO_KEY="your_key"

DATABASE_URL="sqlite:///database/yolohome.db"
FLASK_ENV="development"

FACE_AUTH_THRESHOLD=0.80

```

Khi phát triển offline hoặc chạy unit test, giữ `USE_MOCK_LLM=true`.
Khi muốn gọi Gemini thật, đặt `USE_MOCK_LLM=false` và cấu hình `GEMINI_API_KEY`.

Không commit file `.env` thật lên GitHub. Chỉ commit `.env.example`.

---

### 2.6 Kiểm tra thiết lập:

Chạy toàn bộ test offline trước (không tốn quota API):

```bash
python -m pytest -q
```

Rồi kiểm tra config, database và pipeline lệnh từ đầu đến cuối:

```bash
python -m tools.check_setup
```

Để xác minh toàn bộ cổng, chạy điểm vào chính:

```bash
python system_core/main.py
```
Để chạy Flask dashboard riêng lẻ:

```bash
python web_dashboard/app.py
```

### 2.7 Công cụ chẩn đoán LLM (tùy chọn, có tốn quota):

```bash
python -m tools.probe_gemini     # liệt kê model khả dụng với key của bạn
python -m tools.smoke_gemini     # 10 câu gọi Gemini thật, kiểm tra pipeline
python -m tools.benchmark_llm --models gemini-3.1-flash-lite --rpm 15
```
> Gemini free tier giới hạn 15 request/phút. Luôn truyền `--rpm 15` để lỗi
> quota không làm nhiễu kết quả benchmark.

---

## 3. Cấu trúc và phạm vi dự án

Để tránh xung đột khi hợp nhất, **chỉ làm việc trong thư mục module được chỉ định của bạn**:

`modules/llm_integration/`: Prompt Gemini, parse JSON, mock command parser và tích hợp validation.

`modules/speech_recognition/`: Tích hợp Speech-to-Text.

`modules/face_recognition/`: Nhận diện khuôn mặt và xử lý camera.

`modules/hardware_gateway/`: Giao tiếp Yolo:Bit, Serial, MQTT hoặc IoT gateway.

`services/`: Các service mức ứng dụng như điều phối command, auth flow, logging và rule handling.

`system_core/`: Core abstraction và implementation của design pattern, gồm Command và Strategy.

`config/`: Cấu hình runtime, schema, device registry, alias và capability policy.

`database/`: SQLite schema, script khởi tạo và tài liệu database.

`web_dashboard/`: Flask dashboard và template giao diện.

`tests/`: Unit test và integration test, chia thành `db/`, `llm/`, `pattern/`, `service/`. Toàn bộ chạy offline với LLM mock (không tốn quota): `python -m pytest -q`.

---

## 4. Chiến lược phân nhánh (GitHub Flow)

- `main`: Nhánh "thiêng liêng". Luôn ổn định, sẵn sàng để trình bày. **KHÔNG được động vào**.

---

## 5. Cách thực hiện các nhiệm vụ hàng ngày

### 5.1 Luôn đồng bộ với code mới nhất trước tiên:

```bash

git checkout main
git pull origin main
```

### 5.2 Tạo một nhánh tính năng cụ thể cho công việc của bạn:
Đặt tên nhánh của bạn rõ ràng dựa trên tính năng.

```bash

git checkout -b feature/face-auth-pipeline
# hoặc
git checkout -b bugfix/stt-latency
```

### 5.3 Viết code, kiểm tra cục bộ và commit:

```bash
git add .

git commit -m "Feat: Implement SVM classifier for FaceID"
git push origin feature/face-auth-pipeline
```

---
## 6. Yêu cầu kéo (Pull Request) & Đánh giá (Review)

Sau khi tính năng của bạn hoạt động hoàn hảo trên máy, đã đến lúc hợp nhất nó vào nhánh `main`.

### 6.1 Mở Pull Request (PR):

- Truy cập GitHub và nhấp vào **Compare & pull request** trên nhánh đã đẩy của bạn.

- Đặt nhánh cơ sở là `main`.

### 6.2 Quy tắc đánh giá PR (QUAN TRỌNG):

- **Bạn tuyệt đối không được hợp nhất code của chính mình**.

- Trong mục **Reviewers** ở bên phải, bạn **phải tag người đánh giá chính**.

- Các thành viên khác trong nhóm được khuyến khích review code và Accept PR thay reviewer chính.

### 6.3 Chờ phê duyệt:

- Code của bạn phải nhận được ít nhất **1 Approval** trước khi nút "Merge pull request" được kích hoạt.

- Sau khi PR để phê duyệt, nhấn **Merge pull request**

## 7. Hợp đồng tích hợp Module LLM

Pipeline lệnh LLM đã được siết chặt: an ninh phía server, hội thoại nhiều lượt,
độ bền trước lỗi API, và automation rule edge-triggered. Nếu bạn tích hợp với nó,
có ba hợp đồng cần lưu ý:

1. **`LLMStrategy.parse_and_validate()`** có thêm tham số `pending_command`
   (hội thoại nhiều lượt). Strategy tự viết phải nhận tham số này, nếu không
   `CommandService` sẽ ném `ContractError` ngay lúc khởi động.

2. **Phần cứng `execute_command(action="get_status")`** phải trả về
   `{"status": "success", "state": "on"|"off"|"open"|"closed"}`. Thiếu `"state"`
   thì không crash, nhưng người dùng luôn nhận "không xác định được trạng thái".

3. **`main.py` bắt buộc phải nối Observer**, nếu không automation rule sẽ không
   bao giờ chạy — và **hỏng âm thầm**, không lỗi, không log:
   ```python
   hardware.attach(RuleObserver(rules, service))
   while True:
       hardware.poll_sensors()   # nhịp tim của hệ thống
       time.sleep(2)
   ```

Nguyên tắc an ninh: **LLM là bộ hiểu ý định, không phải bộ ra quyết định an ninh.**
Server quyết định `face_auth` dựa trên config, bỏ qua giá trị LLM trả về. Xem
[`docs/LLM-Command-Strategy-Overview.md`](docs/LLM-Command-Strategy-Overview.md).

---

## 🆘 8. Khắc phục sự cố & Quy tắc chung
- **Không bao giờ đẩy các mô hình AI (.pt, .h5, .bin) lên GitHub:** Tệp `.gitignore` sẽ chặn chúng. Chỉ nên tải về máy cục bộ và đặt chúng vào thư mục `models/`.

- **Chạy main gateway trước khi PR:** Luôn kiểm tra mô-đun của bạn bằng cách chạy `python system_core/main.py` để đảm bảo nó không làm hỏng trạng thái ứng dụng toàn cục.

- **Gemini trả 404 cho model trước đây vẫn chạy:** Google ngừng cấp model cho user mới. Chạy `python -m tools.probe_gemini` rồi cập nhật `GEMINI_MODEL`.

- **Dashboard luôn hiện "không xác định được trạng thái":** module phần cứng không trả `"state"` từ `get_status`. Kiểm tra log tìm dòng `VI PHẠM HỢP ĐỒNG`.

- **Tạo automation rule nhưng không có gì xảy ra:** `main.py` có lẽ thiếu `hardware.attach(RuleObserver(...))` hoặc vòng lặp `poll_sensors()`. Rule không tự chạy.

- Nếu lỗi nào đó làm bạn chậm tiến độ *hơn 48 giờ (2 ngày)*, hãy đẩy nhánh hiện tại của bạn lên và báo cáo trong nhóm chat để chúng ta có thể cùng nhau giải quyết vấn đề.

--- ***Hãy cùng nhau hợp tác hiệu quả và hoàn thành xuất sắc dự án này. Chúc mọi người code dui dẻ!🍀🍀🍀 ***