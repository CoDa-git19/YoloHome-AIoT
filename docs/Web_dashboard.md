# Đặc tả Thành phần Giao diện Web: Smart Home AI Agent Dashboard

Tài liệu này liệt kê chi tiết các thành phần (components) và phân hệ (modules) giao diện cần thiết để xây dựng hệ thống web dashboard.

---

## 1. Các thành phần chung toàn cục (Global Components)
Đây là các thành phần xuất hiện cố định trên tất cả các trang của Dashboard.

### 1.1. Thanh tiêu đề (Header)
*   **Logo & Tên ứng dụng:** "Smart Home AI Agent Dashboard" (Góc trái, có thể tích hợp trong Sidebar).
*   **Trạng thái hệ thống:** Đèn báo hiệu (chấm xanh) kèm chữ "System Online".
*   **Đồng hồ thời gian thực:** Hiển thị giờ hệ thống (VD: 10:35:42).
*   **Mode:** Nút thay đổi giao diện Light/Dark mode (hiển thị dưới dạng Light Dark theme toggle button) 

### 1.2. Thanh điều hướng bên trái (Sidebar - Dark Theme)
*   **Menu Navigation (Các tab chính):**
    *   Agent Console (Trang chủ thao tác)
    *   Command Log (Lịch sử câu lệnh)
    *   Face Auth Log (Lịch sử nhận diện khuôn mặt)
*   **Device Status (Trạng thái thiết bị trực tiếp):**
    *   Living Room Light (ON/OFF - Có màu trạng thái)
    *   Fan (ON/OFF)
    *   Main Door (CLOSED/OPEN)
*   **Sensor Status (Trạng thái cảm biến trực tiếp):**
    *   Motion Sensor (DETECTED/CLEAR)
    *   Temperature (Giá trị °C)
    *   Humidity (Giá trị %)
    *   Light Level (Low/High)
*   **Connection Status (Trạng thái kết nối - Dưới cùng):**
    *   Gateway Connected (Đèn xanh)
    *   Adafruit IO Connected (Đèn xanh)

### 1.3. Chân trang (Footer)
*   Thông tin bản quyền/Dự án: "Smart Home AI System | YOLOHOME-AIOT - HCMUT | 2026"

---

## 2. Module 1: Agent Console (Trang thao tác chính)
Trang này là nơi tương tác trực tiếp với AI và theo dõi luồng xử lý lệnh theo thời gian thực.

### 2.1. Top Metrics Bar (Thanh thông số tóm tắt)
*   Các thẻ card nhỏ nằm ngang hiển thị lại trạng thái: Temperature, Humidity, Light Level, Motion, Light (Living Room), Fan, Door.

### 2.2. Agent Pipeline Tracker (Thanh tiến trình AI)
*   Hiển thị 5 bước xử lý của hệ thống kèm trạng thái (VD: DONE, PASSED, SUCCESS, hoặc WAIT):
    1. STT / Input
    2. LLM
    3. Validation
    4. Face Auth
    5. Execution

### 2.3. Khu vực Tương tác (Interactive Area - Cột trái)
*   **1. User Input (Voice or Text):** 
    *   Ô nhập text (hoặc hiển thị text từ giọng nói).
    *   Nút "Send".
    *   Hiển thị "Last STT Result" và "Confidence" score.
*   **2. LLM JSON Output:** Khung hiển thị code (Code block) nền đen chứa kết quả JSON từ LLM (intent, room, device, action, requires_auth) kèm nút Copy.
*   **3, 4, 5. Result Cards (Kết quả các bước):**
    *   *Validation Result:* Trạng thái (PASSED/FAILED) và lý do.
    *   *Face Auth Result:* Trạng thái, Tên User (kèm avatar nhỏ), Confidence score.
    *   *Execution Result:* Trạng thái thực thi phần cứng.
*   **System Response:** Đoạn text trả lời của hệ thống (VD: "Đã mở cửa chính thành công").

### 2.4. Khu vực Mini Logs (Cột phải)
*   **Mini Command Log:** Bảng rút gọn hiển thị các lệnh gần nhất (Cột: Time, Transcript, Intent, Device, Room, Result, Auth).
*   **Mini Face Auth Log:** Bảng rút gọn hiển thị các lần quét mặt gần nhất (Cột: Time, User, Confidence, Status, Triggered by, Result).

---

## 3. Module 2: Command Log (Lịch sử Lệnh điều khiển)
Trang quản lý, tìm kiếm và thống kê toàn bộ các lệnh đã gửi vào hệ thống.

### 3.1. KPI Summary Cards (Thống kê tổng quan)
*   Total Commands (Tổng số lệnh)
*   Success Rate (Tỉ lệ thành công - %)
*   Rejected (Số lệnh bị từ chối)
*   Requires Auth (Số lệnh yêu cầu xác thực)
*   Avg Latency (Độ trễ trung bình - ms)

### 3.2. Data Table & Filters (Bảng dữ liệu & Bộ lọc)
*   **Bộ lọc (Filters):** Lọc theo khoảng thời gian (Date Range), Tên thiết bị (All Devices), Kết quả (All Results), Ô tìm kiếm (Search transcript/intent...).
*   **Cột dữ liệu (Columns):** Time, Transcript, Intent, Device, Room, Validation (Badge màu), Auth (Yes/No), Result (Badge màu), Latency (ms).
*   **Pagination:** Nút chuyển trang ở dưới cùng.

### 3.3. Selected Command Details (Panel chi tiết lệnh - Cột phải)
Khi click vào một dòng trong bảng, hiển thị chi tiết:
*   Tóm tắt: Original Transcript, Intent, Device / Room.
*   **LLM JSON Output:** Khung code nền đen chứa JSON.
*   **Status Breakdown:** Liệt kê các bước (Validation, Authentication, Execution) và Latency tổng.
*   **Command Insights:** Biểu đồ/số liệu nhỏ thống kê trong kỳ.

---

## 4. Module 3: Face Auth Log (Lịch sử Xác thực khuôn mặt)
Trang quản lý, tìm kiếm và thống kê các sự kiện liên quan đến an ninh (Face ID).

### 4.1. KPI Summary Cards (Thống kê an ninh)
*   Total Auth Events (Tổng số lần quét)
*   Authorized Rate (Tỉ lệ cấp quyền thành công - %)
*   Unknown Attempts (Số lần người lạ/không nhận diện được)
*   Rejected Access (Số lần bị từ chối truy cập)
*   Avg Confidence (Độ tự tin trung bình - %)

### 4.2. Data Table & Filters (Bảng dữ liệu & Bộ lọc)
*   **Bộ lọc (Filters):** Lọc theo khoảng thời gian, Trạng thái (All Statuses), All Triggered By, Ô tìm kiếm.
*   **Cột dữ liệu (Columns):** Time, User (Avatar tròn + Tên), Confidence (%), Status (AUTHORIZED/UNKNOWN/REJECTED), Triggered by, Device / Room, Action Result.
*   **Pagination:** Nút chuyển trang ở dưới cùng.

### 4.3. Selected Authentication Details (Panel chi tiết xác thực - Cột phải)
Khi click vào một sự kiện quét mặt, hiển thị:
*   **User Profile Card:** Ảnh khuôn mặt chụp được (kích thước lớn), Recognized User, Confidence, Status, Triggered By, Device/Room, Action Result, Note.
*   **Auth Event (JSON):** Khung code nền đen chứa dữ liệu JSON thô của sự kiện.
*   **Security Insights:** Thống kê an ninh tóm tắt phía dưới.
```eof
