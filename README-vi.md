# YoloHome-AIoT - Hệ Thống Nhà Thông Minh Đa Phương Thức

Chào mừng bạn đến với dự án **YoloHome-AIoT**! Kho lưu trữ này chứa mã nguồn cho hệ thống nhà thông minh đa phương thức (FaceID, Nhận diện giọng nói, LLM và phần cứng Yolo:Bit).

Vì nhóm của chúng mình gồm 5 thành viên làm việc trong các lĩnh vực hoàn toàn khác nhau (AI, Giao diện người dùng và Phần cứng), việc tuân thủ nghiêm ngặt quy trình làm việc này là **bắt buộc** để tránh xung đột tích hợp và lỗi biên dịch.

## 🗂️ Cấu trúc dự án

```text
.
├── config/             # Cấu hình runtime, schema, alias và capability
├── database/           # SQLite schema, script khởi tạo và tài liệu database
├── diagrams/           # Sơ đồ kiến trúc và design pattern
├── docs/               # Tài liệu dự án
├── modules/            # Các module AIoT: LLM, face, speech, hardware gateway
├── services/           # Service điều phối logic ứng dụng
├── system_core/        # Core abstraction và implementation của design pattern
├── tests/              # Unit test và integration test theo từng module
├── web_dashboard/      # Flask dashboard UI
├── .env.example        # File mẫu cho biến môi trường
├── docker-compose.yml  # Cấu hình chạy container nếu cần
├── README.md           # Tài liệu tiếng Anh
└── README-vi.md        # Tài liệu tiếng Việt
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
GEMINI_API_KEY="khóa_api_của_bạn"
ADAFRUIT_IO_USERNAME="tên_người_dùng_của_bạn"
ADAFRUIT_IO_KEY="khóa_của_bạn"
DATABASE_URL="sqlite:///database/smart_home.db"
FLASK_ENV="development"

```

Khi phát triển offline hoặc chạy unit test, giữ `USE_MOCK_LLM=true`.
Khi muốn gọi Gemini thật, đặt `USE_MOCK_LLM=false` và cấu hình `GEMINI_API_KEY`.

Không commit file `.env` thật lên GitHub. Chỉ commit `.env.example`.

---

### 2.6 Chạy ứng dụng:

Để xác minh thiết lập của bạn hoạt động đầy đủ, hãy chạy cổng chính:

```bash
python system_core/main.py

```
Để chạy Flask dashboard riêng lẻ:

```bash
python web_dashboard/app.py

```

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

`tests/`: Unit test và integration test theo module hoặc theo phần kiến trúc.

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

## 🆘 7. Khắc phục sự cố & Quy tắc chung
- **Không bao giờ đẩy các mô hình AI (.pt, .h5, .bin) lên GitHub:** Tệp `.gitignore` sẽ chặn chúng. Chỉ nên tải về máy cục bộ và đặt chúng vào thư mục `models/`.

- **Chạy main gateway trước khi PR:** Luôn kiểm tra mô-đun của bạn bằng cách chạy `python system_core/main.py` để đảm bảo nó không làm hỏng trạng thái ứng dụng toàn cục.

- Nếu lỗi nào đó làm bạn chậm tiến độ *hơn 48 giờ (2 ngày)*, hãy đẩy nhánh hiện tại của bạn lên và báo cáo trong nhóm chat để chúng ta có thể cùng nhau giải quyết vấn đề.

--- ***Hãy cùng nhau hợp tác hiệu quả và hoàn thành xuất sắc dự án này. Chúc mọi người code dui dẻ!🍀🍀🍀 ***
