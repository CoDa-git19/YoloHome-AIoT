# Design Principles

Tài liệu này ghi lại **tám nguyên lý** chi phối cách YoloHome-AIoT được viết.
Chúng không phải quy ước phong cách. Mỗi nguyên lý ra đời từ một lỗi có thật
trong quá trình phát triển, và mỗi nguyên lý đều có test khoá lại.

Khi review code hoặc thêm module mới, đây là danh sách để đối chiếu.

---

## 1. Mô hình diễn giải. Server quyết định.

> *The model interprets. The server decides.*

Một thành phần xác suất — LLM hay model nhận diện khuôn mặt — được phép **báo
cáo nó nhận thấy gì**. Nhưng cái biến nhận thức thành **quyền hạn** luôn là cấu
hình phía server.

**Áp dụng ở hai chỗ, cùng một lập luận:**

| Thành phần | Nó báo cáo | Server quyết định |
|---|---|---|
| LLM | `face_auth`, `intent`, `device`, `action` | `validator.enforce_policy()` ghi đè `face_auth` theo `device_capabilities.json` |
| Face model | `person_name`, `confidence` | `AuthService` so `confidence` với `FACE_AUTH_THRESHOLD` |

`enforce_policy()` ghi đè theo **cả hai chiều**: ép `True` khi policy yêu cầu mà
LLM trả `false` (LLM quên, hoặc người dùng cố prompt injection), và ép `False`
khi policy không yêu cầu mà LLM trả `true`.

`AuthService` **cố ý bỏ qua** cờ `authorized` nếu face module có trả về. Cờ đó
chỉ là tham khảo. Tin nó nghĩa là một model bị thay thế có thể tự cấp quyền cho
chính mình.

**Test khoá lại:** `test_security_policy.py`, `test_auth_service.py::test_advisory_authorized_flag_is_ignored`

---

## 2. Fail closed

Thiếu module, camera lỗi, model ném exception, mạng chết, database khoá — tất cả
đều dẫn tới **TỪ CHỐI**. Không có nhánh nào mang nghĩa "vì không chắc nên cho qua".

Chi phí của một lần từ chối nhầm là người dùng quét lại. Chi phí của một lần cho
qua nhầm là cửa nhà mở.

**Điểm cần nhớ:** cờ `face_auth=True` chỉ **định tuyến** lệnh sang `AuthService`,
nó không tự chặn gì. `CommandService.execute_authorized_command()` cũng **không**
kiểm tra lại — nó chạy phần cứng rồi trả `bool`. Vì vậy `AuthService` là **chốt
chặn duy nhất ở thời điểm thực thi**.

Hệ quả trực tiếp: `execute_authorized_command()` chỉ được gọi ở đúng **hai chỗ**
trong toàn hệ thống — `AuthService.authorize_and_execute()` (sau khi xác thực) và
`RuleObserver.update()` (chỉ cho lệnh **không** cần face auth, có chốt chặn riêng).

**Test khoá lại:** mọi test trong `test_auth_service.py` đều assert số lần gọi
`execute_authorized_command()`, không chỉ assert chuỗi trả về. Một test chỉ kiểm
tra câu trả lời sẽ vẫn xanh ngay cả khi hệ thống mở cửa cho người lạ.

---

## 3. Hỏng thì phải báo

Lỗi tệ nhất không phải lỗi làm sập chương trình. Lỗi tệ nhất là lỗi khiến hệ
thống **báo thành công rồi không làm gì cả**.

**Danh mục lỗi âm thầm đã gặp trong dự án này:**

| Lỗi | Biểu hiện | Cách chặn |
|---|---|---|
| Observer chưa attach | Rule lưu vào DB, báo "đã tạo", không bao giờ chạy | `main.py` attach tường minh + `test_observer_pattern.py` |
| `use_mock=True` mặc định | Bỏ qua Gemini hoàn toàn, kết quả trông vẫn đúng | `[Config] USE_MOCK_LLM` in ra lúc boot |
| Thiếu ffmpeg | `transcribe()` trả `""`, trông như model không nghe được | `_check_ffmpeg()` chạy đầu tiên trong công cụ test |
| ABC dự phòng trong `except ImportError` | `isinstance()` trả `False` mà công cụ test vẫn chạy ngon | `_check_strategy_identity()` |
| Đổi tên khoá cảm biến | Mọi automation rule ngừng kích hoạt, không lỗi, không log | 4 khoá được ghi rõ là **hợp đồng**, không phải chi tiết cài đặt |
| Thiếu `"state"` khi `get_status` | Người dùng luôn nhận "không xác định được trạng thái" | `contracts.check_status_result()` log ERROR |
| Nhãn `Unknown` truthy | Người lạ **mở được cửa** | `_as_identity()` + `REJECT_NAMES`, hai lớp |

Nguyên tắc rút ra: **nếu một thứ có thể hỏng mà không ai biết, phải có chỗ hét lên** —
log ERROR, dòng `[Config]` lúc boot, hoặc một test đỏ.

---

## 4. Một sự việc, một nguồn sự thật

Danh sách phòng, thiết bị, hành động, cảm biến, hành động nhạy cảm — mỗi thứ chỉ
được định nghĩa **đúng một lần**, trong file config, rồi lan ra prompt, JSON
schema, validator và router.

Từng có lúc danh sách cảm biến hợp lệ nằm cứng trong `prompt_template.txt` trong
khi cùng danh sách đó đã có trong `command_schema.json` — hai định nghĩa cho một
sự việc, và không có gì phát hiện được khi chúng lệch nhau.

Cùng lý do: `settings.STT_MODEL_NAME` đọc **đúng biến môi trường** mà
`stt_module` đã đọc (`PHOWHISPER_MODEL`), không đặt tên mới.

**Hệ quả về bảo mật, ghi lại chứ không giấu:** quyền ghi vào
`command_schema.json` và `device_capabilities.json` tương đương quyền điều khiển
toàn bộ chính sách xác thực. Rủi ro được **dời chỗ**, không bị loại bỏ.

---

## 5. Test phải chạy offline

Toàn bộ test suite chạy được trên máy **không** cài dlib, **không** có webcam,
**không** có micro, **không** có API key, **không** có phần cứng.

Nếu vi phạm nguyên tắc này thì CI không chạy được, và các thành viên không làm
phần AI cũng bị chặn — Bình (phần cứng) và Danh (dashboard) không cần dlib để
làm việc của mình.

**Ba kỹ thuật dùng để giữ nguyên tắc này:**

1. **Lazy import.** `cv2` / `face_recognition` / `numpy` được import **bên trong
   hàm**, không ở đầu `face_module.py`. Đổi lại, `_wire_face_auth()` nạp sẵn
   chúng lúc boot để lần nhận diện đầu tiên không khựng và lỗi thiếu thư viện
   vẫn lộ ra sớm.
2. **Tách logic thuần khỏi logic cần I/O.** `SvmFaceRecognizer._as_identity()`
   chỉ xử lý chuỗi, nên test được mà không cần camera hay model.
3. **Dependency injection.** `AuthService(logging_service=...)` cho phép tiêm
   LoggingService giả, test không cần database.

**Cảnh báo:** cách "chữa" bằng `pytest.importorskip` là **sai**. Trên máy thiếu
thư viện, test sẽ **âm thầm skip** và pytest vẫn báo xanh — test an ninh quan
trọng nhất biến mất mà không ai biết. Đó chính là lỗi mà nguyên lý 3 cấm.

---

## 6. Một tài nguyên, một chủ sở hữu

Mỗi tài nguyên hệ thống chỉ có **đúng một** chủ sở hữu chịu trách nhiệm mở và
đóng nó.

| Tài nguyên | Chủ sở hữu | Người dùng |
|---|---|---|
| `cv2.VideoCapture` | `MainOrchestrator` | `face_module.capture_frame(cap=...)` |
| Adafruit IO client | `HardwareModule` | `AdafruitPublisher` gọi qua `publish_to_adafruit()` |
| `LLMStrategy` | `MainOrchestrator` | dùng chung giữa `CommandService` và benchmark |

Hai chỗ cùng mở webcam thì trên Windows chỗ thứ hai nhận `None`, và Face Auth từ
chối mọi lệnh **mà không rõ nguyên nhân**.

`capture_frame()` phản ánh nguyên tắc này bằng cờ `owns_capture`: chỉ đóng camera
nếu chính nó mở. Đóng camera của người khác làm mọi lần xác thực sau đó thất bại.

**Ngoại lệ có kiểm soát:** `capture_frame(cap=None)` tự mở tự đóng — chỉ dùng cho
công cụ kiểm tra thủ công chạy độc lập.

---

## 7. Hợp đồng được kiểm tra lúc khởi động

Sai lệch interface phải lộ ra **lúc boot**, không phải giữa lúc demo.

`system_core/contracts.py` kiểm tra chữ ký của `LLMStrategy` và sự tồn tại của
`execute_command()` trên hardware receiver ngay khi khởi tạo. Một strategy thiếu
tham số `pending_command` sẽ ném `ContractError` lúc boot thay vì `TypeError` khi
có người thật đang nói chuyện với bot.

Cùng tinh thần: `settings.check_config()` in cảnh báo lúc import — bật
`ENABLE_FACE_AUTH=true` mà thiếu `face_model.pkl` thì biết ngay, không đợi tới
lúc đứng trước hội đồng mới phát hiện mọi lệnh mở cửa đều bị từ chối.

---

## 8. Ghi lại giả định tin cậy, đừng giấu

Hệ thống nào cũng có những chỗ phải tin. Nguyên tắc không phải là loại bỏ chúng —
mà là **viết ra**.

Ba giả định đã ghi trong code:

- `pickle.load()` trên `models/face_model.pkl` **thực thi mã** trong file. Ai ghi
  được vào file đó thì chiếm được tiến trình gateway.
- Quyền ghi `command_schema.json` / `device_capabilities.json` tương đương quyền
  điều khiển chính sách xác thực.
- `FACE_REQUIRE_BLINK=false` khiến một tấm ảnh in cũng qua được xác thực.
  `check_config()` cảnh báo nếu bật cờ này.

Giả định được ghi lại là một quyết định kỹ thuật. Giả định không được ghi lại là
một lỗ hổng đang chờ.

---

## Danh sách đối chiếu khi thêm module mới

- [ ] Model/LLM chỉ **báo cáo**; quyết định nằm ở server?
- [ ] Mọi đường lỗi đều dẫn tới **từ chối**, không phải cho qua?
- [ ] Có thứ gì hỏng được mà **không ai biết** không? Nếu có, chỗ nào hét lên?
- [ ] Dữ liệu này đã tồn tại ở file config nào chưa?
- [ ] Test chạy được trên máy **không** cài thư viện nặng chứ?
- [ ] Tài nguyên (camera, kết nối, client) có **đúng một** chủ sở hữu chứ?
- [ ] Sai interface thì lộ ra **lúc boot** hay lúc demo?
- [ ] Có giả định tin cậy nào chưa được viết ra không?
