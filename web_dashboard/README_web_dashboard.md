# YoloHome AI — Web Dashboard

Giao diện web cho gateway YoloHome-AIoT. Toàn bộ pipeline thật chạy qua đây:

```
Giọng nói / Văn bản → STT → LLM → Validation → Face Auth → Execution → Ghi log
```

Dashboard **không tự lắp ráp lại hệ thống**. Nó dùng lại `MainOrchestrator`
(`system_core/main.py`) — cùng đúng bộ module mà console dùng — nên chạy web hay
chạy console đều cho ra hành vi giống hệt nhau.

---

## 1. Chạy nhanh

Luôn chạy **từ thư mục gốc project** (không phải trong `web_dashboard/`):

```powershell
python -m web_dashboard.app
```

Mở http://localhost:5000

> **Vì sao phải dùng `-m`?** `app.py` import các package ở gốc project
> (`config`, `services`, `system_core`...). Chạy `python web_dashboard/app.py`
> sẽ khiến Python chỉ nhìn thấy thư mục `web_dashboard/` và báo
> `ModuleNotFoundError: No module named 'config'`.

---

## 2. Hai chế độ: MOCK và THẬT

Hệ thống có **4 công tắc độc lập**. Bạn có thể bật mock cho module này và chạy
thật ở module kia — rất tiện khi chỉ muốn test một phần.

| Biến | `true` (MOCK) | `false` (THẬT) | Cần gì để chạy thật |
|---|---|---|---|
| `USE_MOCK_LLM` | Parser luật cứng, không gọi API | Gọi Gemini | `GEMINI_API_KEY` |
| `USE_MOCK_STT` | Luôn trả `"bật đèn phòng khách"` | PhoWhisper | `ffmpeg` + tải model |
| `USE_MOCK_FACE` | **Luôn** nhận ra `member_1` | Nhận diện SVM thật | `models/face_model.pkl` + webcam |
| `HARDWARE_MODE` | `simulation` — trạng thái giữ trong RAM | `real` — MQTT tới Yolo:Bit | `ADAFRUIT_IO_USERNAME` + `KEY` |

### Cảnh báo an ninh

`USE_MOCK_FACE=true` **không phải cờ tiện lợi mà là cờ vô hiệu hoá an ninh**.
`MockFaceRecognizer` bỏ qua hoàn toàn khung hình và luôn trả `member_1` với
confidence 0.95 — nghĩa là **bất kỳ ai cũng mở được cửa**. Chỉ dùng để demo
đường ống khi chưa có `face_model.pkl`.

---

## 3. Cách chuyển chế độ

### Cách A — sửa `.env` (khuyến nghị, nhớ lâu dài)

Mở file `.env` ở gốc project, sửa các dòng tương ứng:

```env
# --- Chế độ DEMO: chạy được ngay, không cần model hay phần cứng ---
USE_MOCK_LLM=true
USE_MOCK_STT=true
USE_MOCK_FACE=true
HARDWARE_MODE=simulation
```

```env
# --- Chế độ THẬT: cần đủ điều kiện ở bảng mục 2 ---
USE_MOCK_LLM=false
USE_MOCK_STT=false
USE_MOCK_FACE=false
HARDWARE_MODE=real
```

Rồi chạy `python -m web_dashboard.app`.

### Cách B — biến môi trường tạm (chỉ trong phiên terminal hiện tại)

PowerShell **không** hỗ trợ cú pháp `VAR=value command` của Linux/macOS.
Phải đặt biến trước:

```powershell
$env:USE_MOCK_FACE = "true"
$env:USE_MOCK_STT  = "true"
python -m web_dashboard.app
```

Xoá khi không dùng nữa (nếu không nó sẽ **đè lên `.env`** và gây khó hiểu):

```powershell
Remove-Item Env:USE_MOCK_FACE, Env:USE_MOCK_STT -ErrorAction SilentlyContinue
```

> **Bẫy hay gặp:** biến môi trường phiên **luôn thắng** `.env`. Nếu bạn sửa
> `.env` thành `false` mà dashboard vẫn báo MOCK, gần như chắc chắn là biến
> phiên cũ còn sót. Mở terminal mới là cách nhanh nhất.

### Cách C — cờ dòng lệnh (chỉ cho console, không áp dụng cho web)

```powershell
python -m system_core.main --mock-face --mock-stt
python -m system_core.main --voice          # console giọng nói
python -m system_core.main --real-hw        # nối Yolo:Bit thật
```

---

## 4. Xác nhận đang chạy chế độ nào

Không cần đoán. Có 3 chỗ nhìn được ngay:

**a) Sidebar → mục "AI Modules"** — mỗi module có một badge:

| Badge | Ý nghĩa |
|---|---|
| `REAL` / `GEMINI` (xanh) | Đang chạy thật |
| `MOCK` / `SIMULATION` (vàng) | Đang giả lập |
| `UNAVAILABLE` (đỏ) | Không nạp được (thiếu model/thư viện) |

Nếu có gì bất thường, một ô cảnh báo màu vàng sẽ giải thích hậu quả cụ thể
(ví dụ: *"Thiếu models/face_model.pkl: mọi lệnh mở cửa sẽ bị từ chối"*).

**b) Log lúc khởi động** — terminal in ra ngay khi chạy:

```
[Config] USE_MOCK_LLM = True
[Config] HARDWARE_MODE = simulation
[STT] MOCK mode - canned transcript, no model download.
[FaceID] MOCK mode - ALWAYS authorizes. Demo only!
```

**c) API** (dùng khi debug):

```powershell
curl http://localhost:5000/api/status
```

---

## 5. Kịch bản test

### 5.1. Test chế độ MOCK (không cần model hay phần cứng)

Đặt cả 4 công tắc về mock rồi thử các câu sau trong ô **User Input**:

| Câu lệnh | Kết quả mong đợi |
|---|---|
| `bật đèn phòng khách` | Execution `SUCCESS`, sidebar đổi Light → `ON` |
| `tắt quạt phòng ngủ` | Execution `SUCCESS` |
| `đèn phòng khách đang thế nào` | Trả lời trạng thái thật của thiết bị |
| `mở cửa chính` | Face Auth `PASSED` (mock), cửa mở |
| `đóng cửa chính` | Không cần Face Auth |
| `bật đèn` → rồi `phòng khách` | **Multi-turn**: bot hỏi lại, sau đó thực thi |
| `nếu nhiệt độ trên 30 thì bật quạt phòng khách` | Tạo automation rule (`execution: success`) |
| `bật tivi phòng khách` | `registry_request` (thiết bị chưa đăng ký) |

Kiểm tra thêm: sang tab **Command Log** và **Face Auth Log** — mọi lệnh vừa chạy
phải xuất hiện với đầy đủ KPI, bộ lọc, phân trang, và panel chi tiết khi bấm vào
một dòng.

### 5.2. Test STT thật (micro)

**Chuẩn bị — cài ffmpeg:**

```powershell
# Bắt buộc: chuyển webm của trình duyệt sang wav mono 16kHz cho PhoWhisper
winget install Gyan.FFmpeg

# MỞ TERMINAL MỚI rồi kiểm tra (PATH chỉ nạp lại ở tiến trình mới)
ffmpeg -version
```

**Chuẩn bị — cài thư viện Python:**

```powershell
# torch bản CPU: nhẹ hơn nhiều so với bản mặc định (vốn kèm CUDA)
pip install torch --index-url https://download.pytorch.org/whl/cpu

pip install "transformers==5.14.1"
```

`soundfile`, `sounddevice` và `numpy` thường đã có sẵn. `sounddevice` chỉ dùng
cho console `--voice`; đường web **không cần** nó.

> Nếu pip báo `IncompleteRead` giữa chừng (đứt mạng), chạy lại với
> `pip install --retries 10 --timeout 120 <gói>`.

**Chạy:**

```powershell
Remove-Item Env:USE_MOCK_STT -ErrorAction SilentlyContinue
python -m web_dashboard.app
```

**Cách test:**

1. Sidebar → `STT` phải hiện badge xanh `REAL`.
2. Bấm nút micro cạnh ô nhập → trình duyệt xin quyền → **Cho phép**.
3. Nút chuyển sang màu đỏ và nhấp nháy = đang ghi. Nói rõ: *"bật đèn phòng khách"*.
4. Bấm lại nút để dừng. Ô nhập chuyển sang *"Đang nhận dạng giọng nói..."*.
5. Câu vừa nói được **điền thẳng vào ô nhập lệnh**, rồi hệ thống **tự xử lý
   ngay** — không cần bấm Send.

> **Vì sao tách làm hai bước?** Nút micro gọi `/api/transcribe` trước để chữ
> hiện ra ngay khi nhận dạng xong, rồi mới gọi `/api/command`. Gộp một bước
> (`/api/voice`) thì bạn chỉ thấy transcript khi TOÀN BỘ pipeline xong — với
> lệnh mở cửa là sau 15 giây quét mặt, suốt thời gian đó màn hình trống trơn.

**Lưu ý quan trọng:**

- **Lần đầu sẽ rất lâu** (vài phút): `transformers` tải model `PhoWhisper-base`
  (vài trăm MB). Chỉ tải một lần rồi cache lại.
- Giữ nút ghi **ít nhất 1–2 giây**. Đoạn quá ngắn hoặc im lặng sẽ trả transcript
  rỗng → dashboard báo *"Mình chưa nghe rõ"*.
- Micro **chỉ hoạt động trên `localhost` hoặc HTTPS**. Mở dashboard bằng địa chỉ
  IP LAN qua `http://` sẽ bị trình duyệt chặn — đây là chính sách bảo mật của
  trình duyệt, không phải lỗi code. Xem mục 6 để mở đúng cách.
- Nếu STT chưa sẵn sàng, nút micro tự động bị **vô hiệu hoá** kèm tooltip giải thích.
- Ô **STT Confidence** hiển thị `N/A` là đúng: PhoWhisper không trả về điểm tin cậy
  cho mỗi câu. Dashboard không bịa ra con số trông như thật.

**Về model fine-tune của nhóm:**

`modules/speech_recognition/finetune_final/` chứa config và tokenizer của bản
fine-tune, nhưng **file trọng số bị `.gitignore` loại trừ** (`*.safetensors`,
`*.bin` — dòng 58–59), giống hệt cách `models/face_model.pkl` được xử lý. Muốn
dùng bản fine-tune, xin file `model.safetensors` từ người train rồi đặt vào
thư mục đó, và khai báo trong `.env`:

```env
PHOWHISPER_MODEL=modules/speech_recognition/finetune_final
```

Không khai báo thì hệ thống dùng model công khai `vinai/PhoWhisper-base`
(tự tải ~290MB lần đầu). Đây là mặc định trong `config/settings.py`.

> **Lưu ý về hai nguồn khai báo model.** `stt_module.py` đặt mặc định là thư mục
> fine-tune, còn `config/settings.py` đặt mặc định là `vinai/PhoWhisper-base`.
> `main.py` truyền tường minh `settings.PHOWHISPER_MODEL` xuống, nên **giá trị
> trong settings luôn thắng** — bản fine-tune sẽ KHÔNG được dùng cho tới khi
> bạn khai báo `PHOWHISPER_MODEL` trong `.env`.

### 5.3. Test Face Auth thật (webcam)

**Chuẩn bị — cài thư viện trên Windows:**

`pip install -r requirements.txt` hiện **thất bại** (pin trùng mâu thuẫn giữa
`numpy` / `torch`), nên phải cài từng gói. Thứ tự dưới đây đã được kiểm chứng
trên Windows + Python 3.10:

```powershell
pip install opencv-python scikit-learn

# dlib: dùng bản BUILD SẴN. `pip install dlib` sẽ biên dịch từ source và
# đòi CMake + Visual Studio Build Tools - thường thất bại trên Windows.
pip install dlib-bin

# --no-deps là BẮT BUỘC: nếu không, pip kéo về `dlib` bản source và hỏng.
pip install --no-deps face_recognition
pip install face_recognition_models Click Pillow

# setuptools 81+ đã GỠ BỎ pkg_resources, mà face_recognition_models cần nó.
# Thiếu bước này, face_recognition gọi quit() và làm THOÁT cả tiến trình.
pip install "setuptools==80.10.2"
```

**Đặt model và kiểm tra:**

```powershell
# 1. Chép file model đã train vào:  models/face_model.pkl
# 2. Kiểm tra toàn bộ 3 lớp trước khi chạy dashboard
python -m tools.check_camera

# 3. Tắt mock face rồi chạy
Remove-Item Env:USE_MOCK_FACE -ErrorAction SilentlyContinue
python -m web_dashboard.app
```

`python -m tools.check_camera` phải báo `OK` cả 3 lớp thì Face Auth mới chạy
thật được. Xem thêm mục 5.3.1 bên dưới.

**Cách test:**

1. Sidebar → `Face Auth` phải hiện badge xanh `REAL`.
2. Gõ `mở cửa chính` → hệ thống bật webcam trên **máy chạy server**.
3. Nhìn vào camera và **chớp mắt** (bắt buộc, do `FACE_REQUIRE_BLINK=true`).
4. Kết quả hiện ở thẻ **4. Face Auth**: tên người + độ tin cậy.

**Các trường hợp cần test:**

| Tình huống | Kết quả đúng |
|---|---|
| Người nhà, có chớp mắt | `PASSED` → cửa mở |
| Người lạ | `FAILED` → *"Độ tin cậy dưới ngưỡng cho phép"* |
| Không có ai trước camera | `FAILED` sau `FACE_SCAN_TIMEOUT_SECONDS` (mặc định 15s) |
| Giơ ảnh in trước camera | `FAILED` (không chớp mắt được) |

**Lưu ý về camera:**

- Camera nằm ở **máy chạy Flask**, không phải máy đang mở trình duyệt. Đây là
  thiết kế đúng: camera cửa nhà thì gắn ở cửa nhà.
- **Live Scanner hiện ngay trong trang web**, không mở cửa sổ OpenCV trên máy
  chủ (`FACE_SHOW_WINDOW = False` trong `app.py`). Khi bạn gửi lệnh cần xác
  thực, khung hình camera kèm chữ *"Please Blink..."* xuất hiện ngay dưới thẻ
  **4. Face Auth**, và tự tắt khi có kết quả.

  Lý do bỏ cửa sổ desktop: Windows chặn tiến trình nền giành tiêu điểm khi bạn
  đang ở trình duyệt, nên cửa sổ chỉ nhấp nháy dưới taskbar và phải bấm mới
  hiện - mất vài giây trong tổng số 15 giây quét. Thêm nữa, `cv2.imshow()` chạy
  trong thread worker của Flask có thể treo hẳn request trên Windows.
- Máy có nhiều webcam (hoặc có OBS/DroidCam) thường đẩy webcam thật sang index
  1–2. Chỉnh `CAMERA_INDEX` trong `.env` nếu quét không thấy gì.
- **Fail closed:** thiếu model, camera lỗi, hay hết giờ đều dẫn tới TỪ CHỐI,
  không bao giờ "vì không chắc nên cho qua".

### 5.3.1. Công cụ chẩn đoán webcam

Face Auth hỏng có thể do 3 lớp rất khác nhau, nhưng trên dashboard đều hiện ra
giống hệt nhau: *"lệnh mở cửa bị từ chối"*. Script này tách bạch từng lớp:

```powershell
python -m tools.check_camera            # kiểm tra nhanh cả 3 lớp
python -m tools.check_camera --scan     # quét index 0..4 để tìm webcam
python -m tools.check_camera --save     # chụp 1 ảnh ra data/camera_test.jpg
python -m tools.check_camera --preview  # xem trực tiếp (Esc để thoát)
```

| Lớp | Kiểm tra | Trả lời câu hỏi |
|---|---|---|
| 1 | OpenCV mở được webcam | **Webcam có chạy không** |
| 2 | `dlib` / `face_recognition` | Phát hiện được khuôn mặt không |
| 3 | `models/face_model.pkl` | Biết ai là ai không |

Chỉ cần **lớp 1 đạt** là đã trả lời được "webcam có hoạt động không". Lớp 2–3
quyết định việc *nhận ra ai*, không phải việc *mở được camera*.

`--save` là cách chắc chắn nhất: mở `data/camera_test.jpg` ra xem, thấy đúng
hình mình thì camera hoạt động thật — chứ không phải "mở được nhưng trả frame đen".

> **Lưu ý về phiên bản scikit-learn.** Model được train bằng scikit-learn 1.9.0.
> Nếu máy bạn chạy Python 3.10 thì bản cao nhất chỉ tới 1.7.2, và khi nạp model
> sẽ có `InconsistentVersionWarning`. Đã kiểm chứng là SVM vẫn tính đúng
> (`predict_proba` tổng bằng 1.0, đủ 6 class), nhưng muốn khớp hoàn toàn thì
> cần Python 3.11+ để cài được scikit-learn 1.9.0.

### 5.4. Test phần cứng thật (Yolo:Bit)

```env
HARDWARE_MODE=real
ADAFRUIT_IO_USERNAME=<tên tài khoản>
ADAFRUIT_IO_KEY=<key>
```

Kiểm tra: sidebar → `Adafruit IO Connected` phải sáng xanh; giá trị cảm biến
trong sidebar phải đổi theo dữ liệu Yolo:Bit publish (mỗi ~10 giây).

> Ở chế độ `real`, cảm biến **chưa có dữ liệu sẽ không hiển thị** thay vì hiện
> số bịa. Ô cảm biến trống nghĩa là MQTT chưa nhận được message nào.

### 5.5. Test giao diện trên điện thoại

Dashboard đã được tối ưu cho màn hình nhỏ:

| Thành phần | Hành vi trên mobile |
|---|---|
| Sidebar | Trượt đè lên nội dung (drawer) + nền mờ, **không** ép layout |
| Header | Tiêu đề rút gọn thành "YoloHome AI", ẩn đồng hồ |
| Thẻ metric | 2 cột thay vì 4 |
| Pipeline 5 bước | Cuộn ngang trong khung riêng |
| Bảng log | Cuộn ngang, giữ nguyên độ rộng cột |
| Bảng + panel chi tiết | Xếp dọc thay vì cạnh nhau |

Test nhanh không cần điện thoại: mở DevTools (F12) → chế độ thiết bị → chọn
iPhone SE (375px). Trang **không được** cuộn ngang ở cấp toàn trang.

---

## 6. Mở dashboard bằng Chrome (micro & camera)

Micro và camera trong hệ thống này nằm ở **hai nơi khác nhau** — đây là chỗ rất
dễ hiểu nhầm:

| | Chạy ở đâu | Có bị chặn khi mở qua IP LAN? |
|---|---|---|
| **Micro** | Trình duyệt (`getUserMedia` → `MediaRecorder`) | Có |
| **Camera** | Server Python (`cv2.VideoCapture`) | Không |

Face Auth mở webcam bằng OpenCV **trên máy chạy Flask**, không thông qua trình
duyệt. Nên dù bạn mở dashboard bằng địa chỉ nào, camera vẫn hoạt động bình
thường — trình duyệt không cần xin quyền camera và cũng không có gì để chặn.

Chỉ **micro** bị ràng buộc. Trình duyệt chỉ cho dùng micro trong *secure
context*: `https://`, `localhost`, hoặc `127.0.0.1`. Mở bằng
`http://192.168.1.10:5000` thì Chrome **không tạo ra** `navigator.mediaDevices`,
và dashboard báo *"Trình duyệt không cho dùng micro"*. Đây là chính sách bảo mật
của trình duyệt, không phải lỗi code, và không tắt được từ phía server.

### Cách 1 — Dùng localhost (đơn giản nhất)

Nếu bạn ngồi ngay máy đang chạy server, mở:

```
http://localhost:5000
```

Micro và camera đều chạy, không cần cấu hình gì thêm.

### Cách 2 — Vẫn muốn mở bằng địa chỉ IP LAN trên Chrome

Khai báo cho Chrome coi origin đó là an toàn:

1. Mở `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
2. Chuyển sang **Enabled**, rồi gõ địa chỉ dashboard vào ô bên cạnh:

   ```
   http://192.168.1.10:5000
   ```

   (thay bằng đúng IP máy bạn — xem bằng lệnh `ipconfig`)
3. Bấm **Relaunch** để Chrome khởi động lại.

Sau đó micro dùng được bình thường.

**Lưu ý:**

- Flag chỉ có tác dụng trên **chính máy đã bật nó**, không ảnh hưởng thiết bị khác.
- Phải nhập **đúng cả cổng**: `http://192.168.1.10:5000`, chứ
  `http://192.168.1.10` sẽ không khớp.
- Chrome trên Android cũng vào `chrome://flags` để làm tương tự.
- IP LAN có thể đổi khi router cấp lại DHCP — lúc đó phải sửa lại flag.

---

## 7. Xử lý sự cố

| Triệu chứng | Nguyên nhân & cách sửa |
|---|---|
| `ModuleNotFoundError: No module named 'config'` | Chạy sai cách. Dùng `python -m web_dashboard.app` từ gốc project. |
| Nói gì cũng ra `"bật đèn phòng khách"` | Đang ở `USE_MOCK_STT=true`. Mock bỏ qua audio, trả câu cố định. |
| `ffmpeg` không nhận dù đã cài | PATH chưa nạp lại. **Đóng hẳn terminal/VS Code rồi mở lại.** |
| Mọi lệnh mở cửa đều bị từ chối | Thiếu `models/face_model.pkl`. Sidebar sẽ báo rõ. |
| Nút micro bị mờ, không bấm được | STT chưa sẵn sàng — xem badge STT ở sidebar. |
| Trình duyệt không xin quyền micro | Đang mở qua `http://` + IP LAN. Xem **mục 6** cho 2 cách khắc phục. |
| Sửa `.env` mà không có tác dụng | Biến môi trường phiên đang đè lên. Mở terminal mới. |
| Dashboard trống, không có log | Chưa gửi lệnh nào, hoặc DB vừa được khởi tạo. |

---

## 8. Chia sẻ / Deploy

### Muốn xem thử dashboard? Nhắn Danh

Dashboard được chia sẻ qua **Cloudflare Tunnel**, chạy trên máy của Danh bằng
tài khoản Cloudflare của Danh. **Nhắn Danh để lấy link `https://` đang mở** —
link chỉ sống khi máy đang chạy, và đổi mỗi lần khởi động lại.

Mở link đó trên máy tính hay điện thoại đều được, không cần chung WiFi.

### Tự dựng tunnel (nếu bạn chạy máy mình)

```powershell
# Cửa sổ 1
python -m web_dashboard.app

# Cửa sổ 2
cloudflared tunnel --url http://localhost:5000
```

Nó in ra URL `https://<ngẫu-nhiên>.trycloudflare.com` trong một khung viền.

Cài đặt: `winget install Cloudflare.cloudflared`, rồi **mở terminal mới**.
Nếu gõ `cloudflared` không nhận, dùng đường dẫn đầy đủ:
`& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:5000`

Vì sao chọn Cloudflare thay vì ngrok:

| | Cloudflare Quick Tunnel | ngrok free |
|---|---|---|
| Tài khoản / authtoken | **Không cần** | Bắt buộc |
| Trang cảnh báo trung gian | Không | Có, phải bấm "Visit Site" |
| Hạn mức băng thông tháng | Không áp | Có — Live Scanner (MJPEG) ngốn nhanh |
| Bản mới trên winget | Có | Không (kẹt ở 3.3.1, quá cũ để đăng nhập) |

**Những điều cần biết khi dùng link:**

- **HTTPS thật nên micro dùng được** trên điện thoại, không phải chỉnh
  `chrome://flags` như khi mở bằng IP LAN.
- **Camera vẫn là webcam của máy chạy server**, không phải camera điện thoại.
  Nên khi gõ `mở cửa chính` từ xa, phải có người ngồi trước webcam đó thì Face
  Auth mới pass; không thì hết 15 giây rồi bị từ chối.
- Cloudflare ghi rõ Quick Tunnel dành cho **thử nghiệm**, không cam kết uptime.
  Giới hạn 200 request đồng thời (vượt → HTTP 429).
- Máy chạy server phải bật; tắt terminal nào cũng mất kết nối.

> **Trước khi chia sẻ link:** kiểm tra `.env` có `USE_MOCK_FACE=false`. Nếu để
> `true` thì mock luôn xác thực thành công — **bất kỳ ai có link cũng mở được
> cửa**. Dashboard hiện chưa có lớp đăng nhập nào.

### Deploy lên cloud — các ràng buộc

- **Điều khiển phần cứng từ xa vẫn chạy.** Lệnh đi qua Adafruit IO MQTT
  (Internet), không cần chung mạng LAN với Yolo:Bit.
- **Face Auth không chạy được trên cloud** — server cloud không có webcam.
- **STT thật rất khó** — `torch` + `transformers` vượt giới hạn RAM/dung lượng
  của hầu hết gói free.
- **SQLite sẽ mất dữ liệu** khi container restart (ổ đĩa tạm).

---

## 9. Tham chiếu API

| Endpoint | Mô tả |
|---|---|
| `GET /api/status` | Cảm biến, thiết bị, trạng thái module, kết nối |
| `POST /api/command` | Gửi lệnh văn bản `{transcript, session_id}` |
| `POST /api/transcribe` | Gửi audio (multipart `audio`) → **chỉ** trả transcript, không chạy lệnh. Nút micro dùng endpoint này |
| `POST /api/voice` | Gửi audio → STT → chạy luôn pipeline (một bước, không có xác nhận) |
| `GET /api/command-log` | Lịch sử lệnh (lọc + phân trang) |
| `GET /api/command-log/summary` | KPI lệnh |
| `GET /api/face-log` | Lịch sử xác thực khuôn mặt |
| `GET /api/face-log/summary` | KPI an ninh |
| `GET /api/face-preview` | Stream MJPEG khung hình quét mặt (Live Scanner) |
