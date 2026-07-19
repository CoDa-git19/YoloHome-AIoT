# Hệ thống Nhận diện Khuôn mặt (Face Recognition Module)

Tài liệu này mô tả chi tiết 2 giai đoạn chính để xây dựng và vận hành module xác thực khuôn mặt cho hệ thống YoloHome-AIoT: **Giai đoạn Huấn luyện mô hình (Training)** và **Giai đoạn Thực thi (Inference)**.

---

## 1. Giai đoạn Huấn luyện Mô hình (Training Phase)

Giai đoạn này tập trung vào việc thu thập dữ liệu thực tế từ các thành viên trong gia đình và huấn luyện một mô hình AI có khả năng phân loại độ chính xác cao.

### 1.1. Thu thập dữ liệu ảnh (Dataset)
Bộ dữ liệu được xây dựng bằng cách thu thập ảnh trên mạng để mô phỏng danh tính các thành viên. Bạn có thể tham khảo toàn bộ dữ liệu ảnh gốc và các script huấn luyện trên Colab tại liên kết dưới đây:

**🔗 [Link Google Drive: Face_recognition Dataset & Colab Notebooks](https://drive.google.com/drive/folders/1x1XhBWTxF0KHK5n6sOTO_CTqfcDZ2btS?usp=sharing)**

- **Số lượng:** Tải khoảng **100 bức ảnh từ Internet** để làm đại diện cho mỗi người (mỗi class).
- **Đa dạng hóa:** Các bức ảnh thu thập cần bao phủ nhiều góc độ và điều kiện khác nhau: nhìn thẳng, nghiêng trái/phải, ánh sáng tốt, ánh sáng yếu, v.v. để mô hình học được đặc trưng tốt nhất.
- **Nhóm "Người lạ" (Unknown):** Tải thêm khoảng 100 bức ảnh của những người ngẫu nhiên (từ internet) và gán vào class `Unknown`. Điều này giúp AI học cách từ chối những khuôn mặt không có trong cơ sở dữ liệu.
- **Tổ chức thư mục:** Dữ liệu cần được lưu trữ theo cấu trúc chuẩn:
  ```text
  dataset/
  ├── [Ten_Thanh_Vien_1]/
  │   ├── img_1.jpg
  │   └── ...
  ├── [Ten_Thanh_Vien_2]/
  ├── Unknown/
  └── ...
  ```

### 1.2. Trích xuất Embedding và Huấn luyện SVM
Sử dụng script Python (hoặc Google Colab) để quét qua thư mục `dataset/`:
1. **Trích xuất đặc trưng (Embedding):** Dùng thư viện `dlib` để chuyển đổi từng khuôn mặt thành một vector 128 chiều (128D).
2. **Huấn luyện mô hình phân loại (SVM):**
   - Sử dụng thuật toán **Support Vector Machine (SVM)** từ thư viện `scikit-learn`.
   - Dữ liệu được chia tỷ lệ **80% Train / 20% Hold-out Test**.
   - **Tối ưu hóa (GridSearchCV):** Chạy dò tìm siêu tham số trên Kernel `rbf` (các giá trị `C` và `gamma`) bằng Stratified 5-Fold Cross Validation.
   - **Chống mất cân bằng dữ liệu:** SVM được cấu hình với `class_weight='balanced'` và `probability=True`.

### 1.3. Đánh giá Mô hình (Evaluation) & Lưu trữ
Hệ thống sử dụng cơ chế **Confidence Threshold >= 80%**. Nếu xác suất dự đoán của SVM dưới 80%, kết quả sẽ bị ép về class `Unknown`.
Mô hình sau đó được đánh giá qua các chỉ số:
- **Accuracy, Precision, Recall, F1-score**.
- **FAR (False Accept Rate):** Tỷ lệ nhận diện nhầm người lạ thành người nhà (càng thấp càng bảo mật).
- **FRR (False Reject Rate):** Tỷ lệ từ chối người nhà.
- Sau khi hoàn tất, kết quả đánh giá sẽ được ghi vào file `evaluation_metrics.txt` và mô hình được nén lại thành file `face_model.pkl`.

---

## 2. Giai đoạn Thực thi (Inference Phase)

File `face_model.pkl` thu được từ giai đoạn 1 sẽ được nạp vào hệ thống Gateway để chạy trong thời gian thực (Real-time).

### 2.1. Tích hợp Liveness Detection (Chống giả mạo)
Để ngăn chặn kẻ gian dùng ảnh in hoặc video phát qua điện thoại để mở khóa, hệ thống yêu cầu người dùng phải **chớp mắt** trước camera.
- Module tính toán **Eye Aspect Ratio (EAR)** dựa trên 6 điểm tọa độ quanh mắt.
- Nếu EAR giảm xuống dưới ngưỡng (VD: `0.20`) rồi tăng lại, hệ thống ghi nhận có một cái chớp mắt thực sự (Liveness Confirmed!).
- Sau khi xác nhận người thật, khung hình sạch mới được chuyển đi để trích xuất đặc trưng và nhận diện tên.

### 2.2. Luồng xử lý trên Gateway
1. **Lệnh bằng giọng nói:** Người dùng ra lệnh cần bảo mật (VD: *"Mở cửa chính"*).
2. **Kích hoạt Camera:** `AuthService` gọi hàm `capture_frame(require_blink=True)` để bật webcam, hiển thị giao diện quét (vẽ khung xanh/vàng).
3. **Phân tích:** Khung hình được chuyển cho `SvmFaceRecognizer`. Hệ thống convert sang ảnh RGB, dò khuôn mặt bằng `face_recognition` và dự đoán bằng `face_model.pkl`.
4. **Phê duyệt lệnh:** Nếu người dùng đúng là thành viên trong nhà, Gateway sẽ xuất lệnh cho phần cứng mở cửa và tự động ghi log khuôn mặt vào cơ sở dữ liệu.

### 2.3. Kiểm thử Độc lập
Bạn có thể kiểm tra tính năng nhận diện và chống giả mạo mà không cần bật cả hệ thống AIoT bằng lệnh:
```bash
python -m tools.test_face
```
Lệnh này giúp bạn dễ dàng canh chỉnh lại ngưỡng chớp mắt `EAR_THRESHOLD` cho phù hợp với ánh sáng phòng và góc đặt camera thực tế.
