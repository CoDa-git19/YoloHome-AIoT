# Setup trên Windows

Tài liệu này ghi lại **chín cái bẫy** đã gặp thật khi dựng môi trường đầy đủ
(LLM + STT + Face Recognition) trên Windows 11 với Python 3.14.

Đọc trước khi cài. Tổng thời gian mất vì chúng: khoảng một buổi.

---

## 0. Đường dẫn dự án PHẢI toàn ký tự ASCII

**Đây là bẫy tốn thời gian nhất, và triệu chứng của nó đánh lạc hướng hoàn toàn.**

```
D:\253\ĐAĐN\YoloHome-AIoT      ← HỎNG
D:\253\DADN\YoloHome-AIoT      ← ĐÚNG
```

`dlib` và `OpenCV` là thư viện C++, mở file bằng API hệ thống dạng ANSI. Ký tự
`Đ` (U+0110) không tồn tại trong bảng mã cp1252 nên chúng **không mở được file**
dù file nằm đúng chỗ và đủ dung lượng.

Thông báo lỗi không hề nhắc tới đường dẫn:

```
RuntimeError: Unable to open D:\...\shape_predictor_68_face_landmarks.dat
```

Rất dễ tưởng file hỏng và tải lại nhiều lần vô ích.

Cách kiểm chứng:

```powershell
mkdir C:\dlibtest -Force
copy "venv\Lib\site-packages\face_recognition_models\models\shape_predictor_68_face_landmarks.dat" C:\dlibtest\
python -c "import dlib; dlib.shape_predictor(r'C:\dlibtest\shape_predictor_68_face_landmarks.dat'); print('mo duoc')"
```

Mở được từ `C:\` mà không mở được từ thư mục dự án → đúng là lỗi đường dẫn.

**Lưu ý về sau:** `cv2.imread()` và `cv2.imwrite()` cũng dính lỗi này. Hiện tại
hệ thống chỉ dùng `cv2.VideoCapture(0)` (camera, không có đường dẫn) nên chưa
gặp — nhưng khi thêm chức năng lưu ảnh khuôn mặt vào `data/` thì sẽ hỏng, và
lần đó khó đoán hơn vì nó chỉ hỏng lúc ghi ảnh chứ không hỏng lúc import.

Đổi tên thư mục xong **phải tạo lại venv** — venv ghi cứng đường dẫn tuyệt đối
bên trong.

---

## 1. numpy phải được quyết định TRƯỚC mọi gói khoa học khác

`requirements.txt` từng ghim `numpy==1.24.3`. Bản đó không có wheel cho Python
3.14, nên pip build từ nguồn bằng MinGW và cho ra một bản numpy mà chính nó
cảnh báo:

```
Numpy built with MINGW-W64 on Windows 64 bits is experimental...
CRASHES ARE TO BE EXPECTED - PLEASE REPORT THEM TO NUMPY DEVELOPERS
```

Kèm theo hàng loạt `RuntimeWarning: invalid value encountered in exp2` ngay khi
nạp module — bản build này tính sai giới hạn số thực.

**Không được bỏ qua.** `dlib`, `face_recognition`, `scikit-learn` đều thao tác
mảng numpy rất nặng; một bản numpy hỏng gây lỗi ngẫu nhiên, khó tái hiện, và
thường nổ đúng lúc demo.

---

## 2. Nâng numpy sau khi đã cài gói khác → sai ABI

Nếu cài `opencv-python` lúc numpy còn 1.x rồi mới nâng numpy lên 2.x:

```
AttributeError: _ARRAY_API not found
ImportError: numpy.core.multiarray failed to import
```

Gói đã biên dịch vẫn giữ liên kết với ABI cũ. Sửa:

```powershell
pip install --force-reinstall --no-cache-dir opencv-python
```

`--no-cache-dir` bắt buộc, nếu không pip lấy lại đúng bản cũ trong cache.

Ứng viên khác có thể dính: `dlib-bin`, `scikit-learn`, `soundfile`, `torch`.

**Cách tránh:** cài numpy 2.x ngay từ đầu, hoặc cài tất cả trong MỘT lệnh để
pip giải phụ thuộc một lần với mục tiêu nhất quán.

---

## 3. `scikit-learn` cũ chặn numpy 2

```
scikit-learn 1.4.1.post1 requires numpy<2.0,>=1.19.5, but you have numpy 2.5.1
```

Dây chuyền ràng buộc:

```
Python 3.14  →  numpy 1.x không có wheel  →  buộc numpy 2.x
             →  buộc scikit-learn >= 1.5
             →  face_model.pkl phải load được bằng sklearn >= 1.5
```

**Việc cần làm với người train model:** hỏi phiên bản dùng trong notebook.

```python
import sklearn, numpy, sys
print(sklearn.__version__, numpy.__version__, sys.version)
```

Nếu train bằng sklearn < 1.5 thì nên nâng trong Colab rồi xuất `.pkl` mới.
Pickle của sklearn thường load được qua các bản minor kèm
`InconsistentVersionWarning`, nhưng "thường" không phải "luôn luôn" — và khi
hỏng thì nó hỏng theo kiểu tệ nhất: **unpickle thành công nhưng dự đoán sai**.

Kiểm tra file model:

```powershell
python -W error::UserWarning -c "import pickle; m = pickle.load(open('models/face_model.pkl','rb')); print(type(m), m.classes_)"
```

`-W error::UserWarning` biến cảnh báo lệch phiên bản thành lỗi, để nó không
trôi qua im lặng.

---

## 4. `face_recognition` kéo `dlib` bản nguồn

Gói khai báo phụ thuộc `dlib>=19.7`. `dlib-bin` cung cấp đúng module `dlib`
khi import, nhưng với pip nó là **một tên gói khác** — nên pip vẫn tải `dlib`
bản nguồn về build, cần CMake + Visual Studio Build Tools, mất 15–20 phút và
hay fail giữa chừng.

Dấu hiệu: màn hình hiện `Building wheel for dlib (setup.py)` rồi đứng im.

```powershell
pip install dlib-bin
pip install face_recognition --no-deps
```

---

## 5. `face_recognition_models` cần `pkg_resources`

```
ModuleNotFoundError: No module named 'pkg_resources'
```

`pkg_resources` đã bị gỡ khỏi `setuptools` từ bản 81, mà `torch` kéo về
setuptools 83.

```powershell
pip install "setuptools<81"
```

**Triệu chứng đánh lạc hướng:** thư viện bọc `except Exception` quanh câu import
rồi in ra một gợi ý sai sự thật:

```
Please install `face_recognition_models` with this command before using `face_recognition`:
pip install git+https://github.com/ageitgey/face_recognition_models
```

Gói đã cài rồi. Cài lại bao nhiêu lần cũng vô ích vì nguyên nhân thật bị nuốt
mất. Chạy thẳng `python -c "import face_recognition_models"` để lộ lỗi thật.

---

## 6. `face_recognition_models` có thể thiếu file `.dat`

Gói chứa ~130 MB dữ liệu model. Nếu build lúc setuptools mới thì data file bị
rớt, chỉ còn code:

```
RuntimeError: Unable to open ...\shape_predictor_68_face_landmarks.dat
```

Kiểm tra:

```powershell
dir venv\Lib\site-packages\face_recognition_models\models
```

Phải có bốn file:

| File | Dung lượng |
|---|---|
| `shape_predictor_68_face_landmarks.dat` | ~99 MB |
| `dlib_face_recognition_resnet_model_v1.dat` | ~22 MB |
| `shape_predictor_5_face_landmarks.dat` | ~9 MB |
| `mmod_human_face_detector.dat` | ~713 KB |

Thiếu thì tải tay:

```powershell
$dir = "venv\Lib\site-packages\face_recognition_models\models"
mkdir $dir -Force
$base = "https://github.com/ageitgey/face_recognition_models/raw/master/face_recognition_models/models"

@(
  "shape_predictor_68_face_landmarks.dat",
  "shape_predictor_5_face_landmarks.dat",
  "dlib_face_recognition_resnet_model_v1.dat",
  "mmod_human_face_detector.dat"
) | ForEach-Object {
    Invoke-WebRequest "$base/$_" -OutFile "$dir\$_"
}
```

File nào chỉ vài KB nghĩa là tải nhầm trang HTML.

---

## 7. `adafruit-io` không cài được trên Python 3.12+

```
ModuleNotFoundError: No module named 'distutils'
OSError: Could not build the egg.
```

Gói dùng `ez_setup.py` (cơ chế bootstrap từ ~2014): tải về `setuptools 4.0.1`
rồi cần `distutils`, đã bị gỡ khỏi Python 3.12.

**Không sửa được.** Nhưng cũng không cần: gói này chỉ phục vụ
`publish_to_adafruit()` trong `hardware_module.py`, và chỗ đó đã bọc
`try/except ImportError` nên thiếu gói cũng không sập.

Hướng xử lý lâu dài: bỏ hẳn, đẩy dữ liệu lên Adafruit IO qua MQTT
(`paho-mqtt`) thay vì REST. Vừa gỡ được phụ thuộc, vừa hết lỗi echo feed.

---

## 8. `ffmpeg` không cài được bằng pip

STT cần nó để chuẩn hoá audio. Thiếu thì `transcribe()` trả chuỗi rỗng **im
lặng** — nhìn như model không nghe được gì.

```powershell
winget install Gyan.FFmpeg
```

Cài xong **phải mở lại terminal** (PATH mới). Nếu dùng terminal tích hợp của
VS Code thì có khi phải đóng cả VS Code.

Không muốn restart:

```powershell
$env:Path += ";$env:LOCALAPPDATA\Microsoft\WinGet\Links"
```

Kiểm tra: `ffmpeg -version`

---

## 9. `SystemExit` lọt qua `except Exception`

`face_recognition` gọi `quit()` khi thiếu gói model. `quit()` ném `SystemExit`,
mà `SystemExit` kế thừa `BaseException` chứ **không** kế thừa `Exception`.

Nghĩa là toàn bộ thiết kế fail-soft bị vô hiệu bởi đúng một lời gọi `quit()`
trong thư viện bên thứ ba: bật `ENABLE_FACE_AUTH=true` mà thiếu gói thì gateway
**thoát ngay giữa lúc boot**.

Đã sửa trong `system_core/main.py`:

```python
except (Exception, SystemExit) as exc:
```

Không dùng `except BaseException` — nó nuốt luôn `KeyboardInterrupt` và Ctrl+C
sẽ không dừng được chương trình.

---

## Kiểm tra sau khi cài xong

```powershell
python -c "import numpy, cv2, dlib, face_recognition, sklearn, torch; print('tat ca ok')"
ffmpeg -version
python -m pytest tests\ -q
```

Cảnh báo `pkg_resources is deprecated` còn lại là vô hại.

Test phải xanh **toàn bộ**. Đây là lúc xác nhận numpy 2 + torch + sklearn không
làm gãy phần LLM — vốn thuần Python và lẽ ra không bị ảnh hưởng, nhưng phải
thấy tận mắt.

Thử từng module độc lập trước khi bật vào hệ thống:

```powershell
python -m tests.modules.stt.test_stt --duration 4
python -m tests.modules.face.test_face
```

Chạy được rồi mới bật trong `.env`:

```ini
ENABLE_FACE_AUTH=true
ENABLE_STT=true
CAMERA_INDEX=0
```

Camera không lên thì thử `CAMERA_INDEX=1` — OBS, Zoom, DroidCam hay chiếm
index 0.

---

## Bài học chung

Tám trong chín bẫy trên có cùng một gốc: **`requirements.txt` ghim phiên bản
từ thời Python 3.10, chạy trên Python 3.14**.

Ghim phiên bản là để tái lập được môi trường. Nhưng ghim rồi bỏ đó nhiều năm
thì nó thành thứ ngược lại: một danh sách các bản không còn tồn tại wheel, buộc
pip build từ nguồn, và mỗi lần build hỏng theo một kiểu khác nhau.

Và cái bẫy thứ chín — `SystemExit` — là ví dụ đời thực cho `Design-Principles.md`
§3: lỗi tệ nhất không phải lỗi làm sập chương trình, mà là lỗi **báo sai nguyên
nhân**. Ở đây có tới hai tầng: thư viện in ra một gợi ý sai, và cơ chế fail-soft
bị vô hiệu mà không ai biết.
