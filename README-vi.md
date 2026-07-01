# YoloHome-AIoT - Hệ Thống Nhà Thông Minh Đa Phương Thức

Chào mừng bạn đến với dự án **YoloHome-AIoT**! Kho lưu trữ này chứa mã nguồn cho hệ thống nhà thông minh đa phương thức (FaceID, Nhận diện giọng nói, LLM và phần cứng Yolo:Bit).

Vì nhóm của chúng mình gồm 5 thành viên làm việc trong các lĩnh vực hoàn toàn khác nhau (AI, Giao diện người dùng và Phần cứng), việc tuân thủ nghiêm ngặt quy trình làm việc này là **bắt buộc** để tránh xung đột tích hợp và lỗi biên dịch.

## 🗂️ Project Structure

```text
.
├── config/                 # System configurations and JSON schemas
│   ├── command_schema.json
│   ├── device_registry.json
│   └── settings.py
├── data/                   # Local data storage (.gitkeep)
├── database/               # Database initialization and schemas
│   ├── README_DB.md
│   ├── init_db.py
│   └── schema.sql
├── docs/                   # Project documentation
│   └── Project-Structure-Overview.md
├── modules/                # Core system modules
│   ├── face_recognition/   # Camera & Face ID processing
│   ├── hardware_gateway/   # Yolo:Bit & MQTT communication
│   ├── llm_integration/    # Gemini/LLM prompt and parsing
│   └── speech_recognition/ # Speech-to-Text processing
├── services/               # Business logic and cross-module services
│   ├── auth_service.py
│   ├── command_service.py
│   ├── logging_service.py
│   └── rule_service.py
├── system_core/            # Main orchestration
│   └── main.py
├── tests/                  # Unit and integration tests
│   ├── test_command_pipeline.py
│   ├── test_llm_module.py
│   └── test_validator.py
└── web_dashboard/          # Flask web UI and HTML templates
    ├── app.py
    └── templates/          # UI views (agent_console, command_log, etc.)
```

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

Hãy hỏi thành viên nhóm phụ trách từng module về các khóa bí mật. Tạo một tệp .env trong thư mục gốc và thêm chúng vào:

```bash
GEMINI_API_KEY="khóa_api_của_bạn"
ADAFRUIT_IO_USERNAME="tên_người_dùng_của_bạn"
ADAFRUIT_IO_KEY="khóa_của_bạn"
DATABASE_URL="sqlite:///database/smart_home.db"
FLASK_ENV="development"

```

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

`modules/speech_recognition/`: Tích hợp PhoWhisper và VAD.

`modules/llm_integration/`: Nhắc API Gemini và phân tích cú pháp JSON.

`modules/face_recognition/`: Nhúng dlib và xử lý khung hình OpenCV.

`modules/hardware_gateway/`: Giao tiếp nối tiếp Yolo:Bit và MQTT.

services/: Chứa logic mức ứng dụng như điều phối command, xác thực, ghi log và xử lý rule.

`system_core/`: Tích hợp hệ thống và các mẫu thiết kế (Observer, Strategy).

`web_dashboard/`: Ứng dụng Flask và giao diện người dùng.

`database/`: Thiết kế SQLite schema, script khởi tạo database và tài liệu database.

`config/`: Cấu hình chung như device registry và command schema.

`tests/`: Unit test và mock integration test.

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
