# Module Responsibilities

Ai sở hữu file nào, module nào được làm gì, và — quan trọng hơn — **không** được
làm gì.

Tài liệu này bổ sung cho `Integration-Contracts.md`. Ở đó là *hình dạng dữ liệu*
đi qua hai seam tích hợp; ở đây là *ranh giới trách nhiệm*.

---

## Bảng sở hữu

| File | Owner | Trách nhiệm | KHÔNG được làm |
|---|---|---|---|
| `modules/llm_integration/llm_module.py` | Uyên | transcript → JSON lệnh đã validate | ra quyết định an ninh |
| `modules/llm_integration/validator.py` | Uyên | enforce policy phía server | tin `face_auth` do LLM trả |
| `modules/speech_recognition/stt_module.py` | Ly | `bytes` → transcript tiếng Việt có dấu | gọi LLM, lowercase, strip dấu câu |
| `modules/face_recognition/face_module.py` | Bình | frame → `person_name` + `confidence` | quyết định authorized, **tự mở camera** |
| `services/auth_service.py` | Uyên | so ngưỡng, thực thi, đóng log | dựng lại `command` |
| `modules/hardware_gateway/hardware_module.py` | Khang | thực thi lệnh, đọc cảm biến | đổi tên 4 khoá cảm biến |
| `services/command_service.py` | Uyên | pipeline transcript → thực thi | kiểm tra lại `face_auth` |
| `services/rule_service.py` | Uyên | lưu và đánh giá automation rule | chạy rule cần face auth |
| `system_core/main.py` | Danh | lắp ráp, vòng cảm biến, sở hữu camera | dựng lại pipeline bằng tay |
| `database/*`, dashboard | Danh | lưu trữ, hiển thị | — |

---

## Ranh giới quan trọng nhất

### `execute_authorized_command()` chỉ được gọi ở hai chỗ

Hàm này **không phải cổng bảo mật**. Nó chạy phần cứng rồi trả `bool`, không
kiểm tra `face_auth`. Gọi nó ngoài hai chỗ dưới đây là mở cửa cho người lạ:

1. `AuthService.authorize_and_execute()` — sau khi `authorized=True`
2. `RuleObserver.update()` — chỉ cho lệnh **không** cần face auth; có chốt chặn
   riêng vì rule chạy tự động, không có ai đứng trước camera để xác thực

### Face module không sở hữu camera

`MainOrchestrator` mở `cv2.VideoCapture` một lần lúc boot và truyền xuống qua
tham số `cap`. Face module **đọc** frame từ đối tượng đó.

Ngoại lệ duy nhất: `capture_frame(cap=None)` tự mở tự đóng — chỉ dùng cho công cụ
kiểm tra thủ công `tests/modules/face/test_face.py`.

> **Sửa đổi Contract B.** Bản gốc ghi "orchestrator đưa **một** frame". Điều đó
> không tương thích với liveness detection: chớp mắt không phát hiện được bằng
> một khung hình. Contract hiện tại: **orchestrator sở hữu `VideoCapture`, face
> module đọc nhiều frame từ nó.** Quyền sở hữu không đổi; chỉ số lượng frame đổi.

### Bốn khoá cảm biến là hợp đồng

```python
{"temperature": ..., "humidity": ..., "light": ..., "motion": ...}
```

`RuleService` khớp `condition["sensor"]` với đúng bốn tên này. Đổi tên khi nối
phần cứng thật sẽ làm **mọi** automation rule ngừng kích hoạt — không lỗi, không
log, không test đỏ.

### `execute_command()` phải trả `state` khi `get_status`

```python
{"status": "success", "state": "on" | "off" | "open" | "closed"}
```

Thiếu `state` thì hệ thống không sập — người dùng chỉ nhận "không xác định được
trạng thái". `contracts.check_status_result()` log ERROR để chuyện đó không âm
thầm.

---

## Quy tắc import

**Trong module có phụ thuộc nặng** (`cv2`, `dlib`, `torch`, `transformers`):

```python
# Ở đầu file: chỉ stdlib và config
import math, pickle
from config.settings import MODELS_DIR

class SvmFaceRecognizer:
    def recognize(self, frame):
        import cv2                 # ← nặng, nằm trong hàm
        import face_recognition
```

Lý do và ngoại lệ: xem `Design-Principles.md` §5.

**Trong công cụ kiểm tra thủ công đặt trong `tests/`** (tên bắt đầu bằng `test_`
nên pytest sẽ import): mọi phụ thuộc nặng phải nằm trong `main()`, kèm
`__test__ = False`.

---

## Test: hai loại, đừng lẫn

| | Test tự động | Công cụ thủ công |
|---|---|---|
| Ví dụ | `test_face_module_contract.py`, `test_auth_service.py` | `test_face.py`, `test_stt.py` |
| Cần gì | không gì cả | webcam, micro, model, người ngồi trước máy |
| Có `assert` | có | không |
| pytest chạy | có | không (`__test__ = False`) |
| Mục đích | khoá hành vi lại | canh chỉnh tham số theo phòng thật |

Công cụ thủ công vẫn nằm trong `tests/` cho tiện tìm, nhưng phải import nhẹ để
không làm đỏ cả suite.

---

## Cờ bật/tắt module

`ENABLE_FACE_AUTH` và `ENABLE_STT` mặc định `false`.

**Tắt không có nghĩa là bỏ qua xác thực.** Hệ thống vẫn fail closed: thiếu face
module thì `door.open` bị từ chối. Cờ quyết định có **nạp** module hay không,
không phải có **kiểm tra** hay không.

Wiring **fail soft**: thiếu thư viện, thiếu model, thiếu webcam thì gateway vẫn
boot, chỉ tính năng đó tắt và có dòng `[Config] ... = OFF (lý do)`. Máy chưa cài
được dlib không được phép làm sập hệ thống của cả nhóm.

`_wire_face_auth()` dựng `face_module`, `camera`, `liveness_capture` và
`auth_service` trong **cùng một** `try/except` — để bất biến *`auth_service` tồn
tại ⟺ `face_module` tồn tại* được đảm bảo bằng cấu trúc code, không bằng kỷ luật.

Vì `AuthService` cần `command_service`, hai lời gọi `_wire_*()` **phải** đứng sau
khối `--- SERVICES ---` trong `MainOrchestrator.__init__`.

---

## Design pattern: dùng ở đâu, giải quyết vấn đề gì

| Pattern | Nơi dùng | Vấn đề nó giải |
|---|---|---|
| Strategy | `LLMStrategy`, `STTStrategy` | đổi Gemini ↔ OpenAI ↔ mock mà không sửa `CommandService` |
| Observer | `Subject` / `Observer` trong `observers.py` | một vòng đọc cảm biến nuôi nhiều nơi tiêu thụ (rule, log, cloud) mà chúng không biết nhau |
| Command | `commands.py` | đóng gói lệnh thành đối tượng có `execute()` / `undo()`, lưu được lịch sử |

Pattern không phải để trang trí báo cáo. Kiểm chứng: bỏ pattern đi thì cái gì
gãy? Bỏ Strategy → `CommandService` phụ thuộc cứng vào Gemini, test phải gọi API
thật. Bỏ Observer → thêm một nơi tiêu thụ dữ liệu cảm biến phải sửa
`HardwareModule`. Bỏ Command → không undo được.
