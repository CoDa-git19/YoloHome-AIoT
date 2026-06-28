# YoloHome-AIoT - Hệ Thống Nhà Thông Minh Đa Phương Thức

Chào mừng bạn đến với dự án **YoloHome-AIoT**! Kho lưu trữ này chứa mã nguồn cho hệ thống nhà thông minh đa phương thức (FaceID, Nhận diện giọng nói, LLM và phần cứng Yolo:Bit).

Vì nhóm của chúng mình gồm 5 thành viên làm việc trong các lĩnh vực hoàn toàn khác nhau (AI, Giao diện người dùng và Phần cứng), việc tuân thủ nghiêm ngặt quy trình làm việc này là **bắt buộc** để tránh xung đột tích hợp và lỗi biên dịch.

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

- Windows (Git Bash/PowerShell):
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

```

### 2.6 Chạy ứng dụng:

Để xác minh thiết lập của bạn hoạt động đầy đủ, hãy chạy cổng chính:

```bash
python system_core/main.py

```

---

## 3. Cấu trúc và phạm vi dự án

Để tránh xung đột khi hợp nhất, **chỉ làm việc trong thư mục module được chỉ định của bạn**:

`speech_recognition/`: Tích hợp PhoWhisper và VAD.

`llm_integration/`: Nhắc API Gemini và phân tích cú pháp JSON.

`face_recognition/`: Nhúng dlib và xử lý khung hình OpenCV.

`hardware_gateway/`: Giao tiếp nối tiếp Yolo:Bit và MQTT.

`system_core/`: Tích hợp hệ thống và các mẫu thiết kế (Observer, Strategy).

`web_dashboard/`: Ứng dụng Flask và giao diện người dùng.

---

## 4. Chiến lược phân nhánh (GitHub Flow)

Chúng mình sử dụng mô hình phân nhánh nghiêm ngặt để bảo vệ tính ổn định của hệ thống.

- `main`: Nhánh "thiêng liêng". Luôn ổn định, sẵn sàng để trình bày. **KHÔNG được động vào**.

- `develop`: Sân chơi tích hợp nơi tất cả các mô-đun gặp nhau. **KHÔNG được viết code trực tiếp ở đây**.

---

## 5. Cách thực hiện các nhiệm vụ hàng ngày

### 5.1 Luôn đồng bộ với code mới nhất trước tiên:

```bash

git checkout develop
git pull origin develop
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

Sau khi tính năng của bạn hoạt động hoàn hảo trên máy, đã đến lúc hợp nhất nó vào nhánh `develop`.

### 6.1 Mở Pull Request (PR):

- Truy cập GitHub và nhấp vào **Compare & pull request** trên nhánh đã đẩy của bạn.

- Đặt nhánh cơ sở là `develop`.

### 6.2 Quy tắc đánh giá PR (QUAN TRỌNG):

- **Bạn tuyệt đối không được hợp nhất code của chính mình**.

- Trong mục **Reviewers** ở bên phải, bạn **phải tag người đánh giá chính**.

- Các thành viên khác trong nhóm được khuyến khích review code và Accept PR thay reviewer chính.

### 6.3 Chờ phê duyệt:

Code của bạn phải nhận được ít nhất **1 Approval** trước khi nút "Merge pull request" được kích hoạt.

Trưởng nhóm tích hợp sẽ kéo nhánh của bạn về máy cục bộ để kiểm tra mức tiêu thụ bộ nhớ và xung đột luồng trước khi phê duyệt.

## 🆘 7. Khắc phục sự cố & Quy tắc chung
- **Không bao giờ đẩy các mô hình AI (.pt, .h5, .bin) lên GitHub:** Tệp `.gitignore` sẽ chặn chúng. Chỉ nên tải về máy cục bộ và đặt chúng vào thư mục `models/`.

- **Chạy main gateway trước khi PR:** Luôn kiểm tra mô-đun của bạn bằng cách chạy `python system_core/main.py` để đảm bảo nó không làm hỏng trạng thái ứng dụng toàn cục.

- Nếu lỗi nào đó làm bạn chậm tiến độ *hơn 48 giờ (2 ngày)*, hãy đẩy nhánh hiện tại của bạn lên và báo cáo trong nhóm chat để chúng ta có thể cùng nhau giải quyết vấn đề.

--- ***Hãy cùng nhau hợp tác hiệu quả và hoàn thành xuất sắc dự án này. Chúc mọi người code dui dẻ!🍀🍀🍀 ***
