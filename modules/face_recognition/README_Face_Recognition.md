# Hướng Dẫn Sử Dụng Thư Mục Chia Sẻ: Face Recognition

Chào mừng bạn đến với dự án Nhận diện Khuôn mặt (Face Recognition). 
Thư mục này chứa toàn bộ dữ liệu (dataset) và các file code Google Colab để kiểm tra ảnh, huấn luyện mô hình và test trực tiếp.

Để đóng góp dữ liệu và kiểm tra xem ảnh của bạn có hợp lệ hay không, vui lòng làm theo các bước sau:

---

## 1. Truy Cập Thư Mục Chia Sẻ

Khi bạn nhận được link thư mục Google Drive này:
1. Mở link thư mục trên trình duyệt.
2. **[QUAN TRỌNG]**: Ở phần tên thư mục trên cùng (hoặc dấu 3 chấm), chọn **Thêm lối tắt vào Drive của tôi** (Add shortcut to Drive).
3. Đặt lối tắt ở thư mục gốc `My Drive` của bạn. Việc này giúp Google Colab có thể tìm thấy thư mục của nhóm.

---

## 2. Cách Up Ảnh Vào Dataset

Để mô hình có thể nhận diện được bạn, bạn cần cung cấp dữ liệu hình ảnh:
1. Mở thư mục **`dataset`** trong Drive.
2. Tạo một thư mục mới với **tên của bạn** (Lưu ý: Viết liền không dấu, ví dụ: `Nguyen_Van_A`).
3. Tải lên (Upload) khoảng **50 - 60 bức ảnh** khuôn mặt của bạn vào thư mục vừa tạo.
   * **Mẹo**: Ảnh nên rõ mặt, đủ sáng, chỉ có 1 mình bạn trong khung hình và ở các góc độ/biểu cảm khác nhau.

---

## 3. Kiểm Tra Ảnh Vừa Up (Check Dataset)

Sau khi up ảnh, bạn cần kiểm tra xem các ảnh đó có hợp lệ để huấn luyện không (có đúng 1 khuôn mặt trong ảnh hay không).
1. Nhấn đúp chuột vào file **`check_dataset_colab.ipynb`**, chọn **Mở bằng Google Colaboratory** ở trên cùng.
2. Khi Colab mở ra, nhìn sang thanh công cụ bên trái, nhấn vào biểu tượng **Thư mục (Files)** 📁.
3. Nhấn vào biểu tượng **Mount Drive** (Thư mục có logo Google Drive) để kết nối Colab với Drive của bạn, sau đó cấp quyền truy cập.
4. Mở rộng cây thư mục: `drive` -> `MyDrive` -> Tìm đến lối tắt thư mục nhóm bạn vừa tạo -> Mở ra sẽ thấy thư mục `dataset`.
5. Bấm chuột phải vào thư mục `dataset`, chọn **Sao chép đường dẫn** (Copy path).
6. Tìm đến ô code cuối cùng trong Colab, sửa biến `dataset_folder` thành đường dẫn bạn vừa copy:
   ```python
   dataset_folder = "/content/drive/MyDrive/[Tên_Lối_Tắt]/dataset"
   ```
7. Nhấn tab **Runtime** (Thời gian chạy) trên menu -> **Run all** (Chạy tất cả). 
8. Kéo xuống dưới cùng để xem kết quả. Nếu thấy cột **"Không đạt"** của bạn > 0, hãy vào Drive xóa các bức ảnh bị lỗi đó đi và thay bằng ảnh khác rõ nét hơn.

---

## 4. Test Mô Hình Ngay Trên Trình Duyệt

Sau khi trưởng nhóm/bạn đã chạy file `train_face_colab.ipynb` và tạo ra file mô hình mới nhất (`face_model.pkl`), bạn có thể test thử ngay lập tức bằng 1 trong 2 file sau:

### Test nhanh bằng ảnh chụp (test_face_colab.ipynb)
1. Mở file **`test_face_colab.ipynb`** bằng Colab.
2. Thực hiện Mount Drive như bước 3 ở trên.
3. Copy đường dẫn file **`face_model.pkl`** từ menu bên trái và dán vào biến `model_path` trong code.
4. Ở ô code cuối cùng, upload 1 bức ảnh test của bạn lên Colab (bấm nút upload 📄 ở menu trái), copy đường dẫn ảnh đó dán vào biến `test_image_path`.
5. Chọn Runtime -> Run all và xem kết quả vẽ khung nhận diện.

---